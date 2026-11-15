"""Tree operations. Every mutation to `nodes` goes through here.

`move` is the centre of the project. It has to reject cycles, and it has to stay
correct when two moves run at once — which the obvious implementation does not,
because each one checks a tree that is still valid at the moment it looks.

See `docs/adr/0003` for why row locks rather than SERIALIZABLE, and
`docs/ARCHITECTURE.md` §5 for the algorithm.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from file_explorer.errors import (
    CycleDetected,
    DepthExceeded,
    DuplicateName,
    FileAsParent,
    InvalidName,
    InvalidType,
    NodeNotFound,
    OperationTimedOut,
    QuotaExceeded,
    StorageError,
    TreeBusy,
)
from file_explorer.limits import DEFAULT_PAGE, MAX_NODES_PER_USER, MAX_PAGE
from file_explorer.names import clean
from file_explorer.repositories.node_repo import NodeRepository, NodeRow

log = logging.getLogger(__name__)

# 40P01 deadlock_detected, 55P03 lock_not_available.
#
# 55P03 is the one that will actually happen. lock_timeout is 3s and
# statement_timeout 5s, so a chain held by a slow transaction surfaces as a
# lock timeout long before the deterministic lock order could produce a
# deadlock. Handling only 40P01 would mean the realistic contention path
# reaches the caller as an unhandled driver error.
RETRYABLE = frozenset({"40P01", "55P03"})

# 57014 query_canceled -- statement_timeout fired.
#
# NOT retryable. The first draft put it in RETRYABLE, which is wrong: a
# cascading delete that exceeded five seconds will exceed five seconds again,
# so retrying burns the timeout twice and still fails. It needs to be
# recognised and mapped, which is a different thing from being retried.
TIMEOUT = "57014"

_MAX_ATTEMPTS = 3

# The enum's two values. Checked here because a bad value reaches PostgreSQL as
# a DataError, not an IntegrityError, so it would bypass _translate entirely.
NODE_TYPES = frozenset({"file", "folder"})

_T = TypeVar("_T")


class _ChainShifted(Exception):
    """The ancestry moved between taking the lock and measuring it.

    Internal to this module. `_with_retry` treats it exactly like a contention
    sqlstate, because that is what it is -- the difference is only that
    PostgreSQL reported this one by giving us a lock on rows that had stopped
    being the chain we needed.
    """


# A page cursor is the (name, id) of the last row the caller saw. It is the sort
# key itself, not an encoded offset, which is what makes it stable while other
# people are writing. The HTTP layer is free to encode it opaquely; the service
# keeps it typed.
Cursor = tuple[str, int] | None
Page = tuple[list[dict[str, object]], Cursor]

# ('', 0) sorts below every stored (name, id): `ck_nodes_name_not_blank`
# guarantees no name is empty, and the empty string is first in byte order.
_FIRST_PAGE: tuple[str, int] = ("", 0)


def _clamp(limit: int) -> int:
    """A caller-supplied page size is an input, not an instruction.

    Unbounded, `limit` is a denial-of-service knob: one request asks for every
    row the owner has and holds a connection while PostgreSQL materialises it.
    """
    return max(1, min(limit, MAX_PAGE))


def _fetch(limit: int) -> int:
    """One more than the page, to learn whether another page exists."""
    return _clamp(limit) + 1


def _start(after: Cursor) -> tuple[str, int]:
    return _FIRST_PAGE if after is None else after


def _constraint_of(exc: IntegrityError) -> str:
    """The constraint a violation names, or "" if the driver did not say."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None) or ""


class TreeService:
    def __init__(
        self, session: Session, max_depth: int, max_nodes: int = MAX_NODES_PER_USER
    ) -> None:
        self._s = session
        self._repo = NodeRepository(session, max_depth)
        self._max_depth = max_depth
        self._max_nodes = max_nodes

    # ---------------------------------------------------------------- retry

    def _with_retry(self, what: str, subject_id: int | None, run: Callable[[], _T]) -> _T:
        """Run `run`, retrying the contention sqlstates a bounded number of times.

        `subject_id` is whatever the operation contends on, which is not always
        the node being changed: `create` contends on its *parent*, because that
        is the chain it locks. Logging it as `node_id` was misleading in exactly
        the incident the line exists for.

        Shared by every operation that takes row locks, so they cannot drift
        apart on which failures are transient. `rename` is in here too: its
        UPDATE waits on the same `FOR NO KEY UPDATE` a move holds, so it sees
        `55P03` for exactly the same reason.

        **Transaction contract.** This does not commit. On a retryable failure
        it *does* roll back, because the failed transaction cannot be
        continued, and that discards any uncommitted work the caller had in the
        same transaction. Call these with nothing else pending.
        """
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return run()
            except _ChainShifted:
                self._s.rollback()
                if attempt == _MAX_ATTEMPTS:
                    raise TreeBusy from None
                log.warning(
                    "ancestry shifted under %s, retrying",
                    what,
                    extra={"attempt": attempt, "op": what, "subject_id": subject_id},
                )
                time.sleep(0.02 * attempt + random.uniform(0, 0.02))
            except DBAPIError as exc:
                sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
                if sqlstate == TIMEOUT:
                    self._s.rollback()
                    log.warning("statement timed out", extra={"op": what, "subject_id": subject_id})
                    raise OperationTimedOut from exc
                if sqlstate not in RETRYABLE:
                    # Roll back for the same reason the retryable path does:
                    # after any error the transaction is aborted, and every
                    # later statement on that connection fails with 25P02. A
                    # caller that maps this to a 4xx and carries on would get a
                    # dead session for the rest of the request.
                    self._s.rollback()
                    # And do not re-raise the driver error. str() on a
                    # DBAPIError renders [SQL: ...] and [parameters: ...] --
                    # the statement, the owner_id and whatever the user
                    # submitted. `_translate` already refuses to let that
                    # escape from the savepoint paths; this is the other half,
                    # and without it the guarantee was only half true.
                    log.exception(
                        "unmapped database error",
                        extra={"op": what, "subject_id": subject_id, "sqlstate": sqlstate},
                    )
                    raise StorageError from exc
                self._s.rollback()
                if attempt == _MAX_ATTEMPTS:
                    log.warning(
                        "gave up on contention",
                        extra={"op": what, "subject_id": subject_id, "sqlstate": sqlstate},
                    )
                    # 503, not the driver error: the request was valid and the
                    # only thing wrong was how busy the tree was.
                    raise TreeBusy from exc
                log.warning(
                    "contention on %s, retrying",
                    what,
                    extra={
                        "attempt": attempt,
                        "op": what,
                        "subject_id": subject_id,
                        "sqlstate": sqlstate,
                    },
                )
                # Linear backoff with jitter. Without jitter two transactions
                # retrying in step keep colliding on the same schedule.
                time.sleep(0.02 * attempt + random.uniform(0, 0.02))
        raise AssertionError("unreachable: the loop either returns or raises")

    # ---------------------------------------------------------------- move

    def move(self, actor_id: int, node_id: int, new_parent_id: int | None) -> None:
        """Re-parent `node_id` under `new_parent_id`, or to the root if None.

        **Transaction contract.** This does not commit — the caller owns the
        transaction and decides when to end it. But on a retryable failure it
        *does* roll back, because the failed transaction cannot be continued.
        That discards any uncommitted work the caller had in the same
        transaction. So either call this with nothing else pending, or be
        prepared to redo that work.

        Retries on `40P01` and `55P03`. `ORDER BY id` on the locking select
        gives every move the same lock order, but a concurrent cascading delete
        locks in scan order, so a deadlock stays reachable — the lock order
        makes it rare, not impossible. A lock timeout is the more likely of the
        two, since `lock_timeout` is three seconds.

        After `_MAX_ATTEMPTS` the driver error propagates. The HTTP layer maps
        it to 503 rather than 500: the request was valid and may succeed later.
        """
        self._with_retry("move", node_id, lambda: self._move_once(actor_id, node_id, new_parent_id))

    def _move_once(self, actor_id: int, node_id: int, new_parent_id: int | None) -> None:
        if node_id == new_parent_id:
            # Also a CHECK constraint, so it cannot reach the database. Caught
            # here to produce an explainable 409 rather than a driver error.
            raise CycleDetected

        # 1. Resolve both ends with plain, non-locking reads.
        #
        # Ownership is established BEFORE any lock is taken. Locking first would
        # mean a foreign id's lock *wait* blocks until the victim commits, which
        # leaks their tree through response time — and a lock wait is not a
        # timing class, so equalising the 404 body would not close it.
        node = self._repo.get(actor_id, node_id)
        if node is None:
            raise NodeNotFound

        if new_parent_id is not None:
            dest = self._repo.get(actor_id, new_parent_id)
            if dest is None:
                raise NodeNotFound
            if dest.type == "file":
                raise FileAsParent

        if node.parent_id == new_parent_id:
            return  # already there; nothing to lock, nothing to write

        # 2. Take the locks.
        locked = self._repo.lock_move_participants(actor_id, node_id, new_parent_id)
        if node_id not in locked:
            # Deleted between the read and the lock.
            raise NodeNotFound

        # 3. Re-check, as a FRESH statement.
        #
        # This is the step that cannot be optimised away. FOR NO KEY UPDATE does
        # not refresh a recursive CTE, so reusing what step 2 returned lets two
        # moves in separate trees commit a 4-cycle between them.
        if new_parent_id is not None:
            if self._repo.is_ancestor_of(actor_id, node_id, new_parent_id):
                raise CycleDetected

            dest = self._repo.get(actor_id, new_parent_id)
            if dest is None:
                raise NodeNotFound  # destination went away while we waited
            if dest.type == "file":
                raise FileAsParent

        # 4. Depth.
        #
        # The bound is depth(destination) + 1 + height(subtree being moved).
        # Measuring only the destination lets two legal 60-deep chains compose
        # into a 120-deep tree in a single move.
        # ancestry() returns the destination itself plus its ancestors, so its
        # length IS the destination's level, counting the root as level 1. A
        # move to the root has no destination row, hence level 0.
        dest_level = 0
        if new_parent_id is not None:
            chain = self._repo.ancestry(actor_id, new_parent_id)
            # Same snapshot mismatch as `create` step 3a, and the same reason.
            # `lock_move_participants` picked its rows under an older snapshot
            # than this walk, so an ancestor added since is unlocked and can be
            # moved deeper before the UPDATE below commits.
            if not {row[0] for row in chain} <= set(locked):
                raise _ChainShifted
            dest_level = len(chain)
        height = self._repo.subtree_height(actor_id, node_id)
        deepest_after_move = dest_level + 1 + height
        if deepest_after_move > self._max_depth:
            raise DepthExceeded

        # 5. Write, inside a savepoint.
        #
        # A unique violation aborts the whole transaction in PostgreSQL, so
        # without the savepoint a duplicate name would surface as a 500 instead
        # of a 409. The index stays the enforcer — a pre-check would not help,
        # because nothing locks the destination's existing children, so one of
        # them can be renamed into a collision after the check.
        try:
            with self._s.begin_nested():
                rows = self._repo.set_parent(actor_id, node_id, new_parent_id)
        except IntegrityError as exc:
            raise self._translate(exc) from exc

        if rows == 0:
            raise NodeNotFound

    # -------------------------------------------------------------- create

    def create(self, actor_id: int, parent_id: int | None, name: str, node_type: str) -> int:
        """Create a node under `parent_id`, or at the root if None. Returns its id."""
        return self._with_retry(
            "create", parent_id, lambda: self._create_once(actor_id, parent_id, name, node_type)
        )

    def _create_once(self, actor_id: int, parent_id: int | None, name: str, node_type: str) -> int:
        if node_type not in NODE_TYPES:
            raise InvalidType

        # Normalise before anything else. What is stored is the cleaned form,
        # because the uniqueness indexes compare stored bytes -- so this is
        # what stops `café` and `cafe` + a combining accent becoming two
        # siblings nobody can tell apart.
        name = clean(name)

        # One account must not be able to fill the database. Checked here
        # rather than by a constraint, which would have to serialise every
        # insert for this owner to count them.
        #
        # Two concurrent creates can both pass this, so the ceiling is soft by
        # the number of requests in flight. A hard one costs a per-owner
        # counter row that every create must lock, which is a worse trade for
        # a limit that exists to stop scripts rather than to bill anyone.
        if self._repo.count_for_owner(actor_id) >= self._max_nodes:
            raise QuotaExceeded

        level = 0
        if parent_id is not None:
            # 1. Resolve with a plain read, scoped by owner. Same reasoning as
            #    `move`: ownership is settled before any lock, so a foreign id
            #    never makes the caller wait on a stranger's transaction.
            parent = self._repo.get(actor_id, parent_id)
            if parent is None:
                raise NodeNotFound
            if parent.type == "file":
                raise FileAsParent  # I5 -- a file is always a leaf

            # 2. Lock the parent's chain, then measure it.
            #
            #    The lock is what makes the measurement mean anything. Without
            #    it a concurrent move can re-parent an ancestor between the
            #    measurement and the insert, and the new node lands past the
            #    cap -- which matters more than I7 alone, because both
            #    recursive walks are bounded at `max_depth` and an over-deep
            #    chain truncates them. A truncated ancestry makes the cycle
            #    check fail open, so the depth cap is load-bearing for I1.
            locked = self._repo.lock_ancestry(actor_id, parent_id)
            if parent_id not in locked:
                raise NodeNotFound  # deleted between the read and the lock

            # 3. Measure as a FRESH statement, after the locks are held.
            #    `FOR NO KEY UPDATE` does not refresh a recursive CTE, so the
            #    ids returned by step 2 are not a usable ancestry.
            #
            #    `ancestry` returns the parent plus its ancestors, so its
            #    length IS the parent's level, counting the root as level 1.
            chain = self._repo.ancestry(actor_id, parent_id)

            # 3a. The two statements used two snapshots, so check they agree.
            #
            #     Step 2 chose WHICH rows to lock from a CTE under its own
            #     snapshot, and `LockRows` sits above that plan -- it does not
            #     re-run the CTE after a lock wait. If an ancestor was
            #     re-parented while we waited, we now hold locks on a chain
            #     that no longer exists while measuring one that does, and the
            #     part we never locked is free for another move to push deeper
            #     before our insert lands.
            #
            #     Holding every row we measured is what makes the measurement
            #     stable: a move of any of them has to take `FOR NO KEY UPDATE`
            #     on the row it moves, which we are holding.
            if not {row[0] for row in chain} <= set(locked):
                raise _ChainShifted

            level = len(chain)

        # 4. The new node sits one below its parent.
        if level + 1 > self._max_depth:
            raise DepthExceeded

        # 5. Insert, inside a savepoint -- a unique violation aborts the whole
        #    transaction in PostgreSQL, so without it a duplicate name would
        #    surface as a 500 rather than a 409.
        #
        #    No pre-check for the duplicate: nothing locks the parent's
        #    existing children, so one of them can be renamed into a collision
        #    between the check and the insert. The index is the enforcer.
        try:
            with self._s.begin_nested():
                node_id = self._repo.insert_node(actor_id, parent_id, name, node_type)
        except IntegrityError as exc:
            raise self._translate(exc) from exc
        return node_id

    # -------------------------------------------------------------- rename

    def rename(self, actor_id: int, node_id: int, new_name: str) -> None:
        """Rename a node in place.

        No depth or cycle consequences -- a rename moves nothing. The only ways
        it can fail are "not yours or not there" and "that name is taken here",
        and the second is left to the unique index for the same reason `move`
        leaves it there.
        """
        self._with_retry("rename", node_id, lambda: self._rename_once(actor_id, node_id, new_name))

    def _rename_once(self, actor_id: int, node_id: int, new_name: str) -> None:
        new_name = clean(new_name)

        node = self._repo.get(actor_id, node_id)
        if node is None:
            raise NodeNotFound

        if node.name == new_name:
            return  # nothing to write, and no reason to bump updated_at

        try:
            with self._s.begin_nested():
                rows = self._repo.set_name(actor_id, node_id, new_name)
        except IntegrityError as exc:
            raise self._translate(exc) from exc

        if rows == 0:
            raise NodeNotFound  # deleted while we were deciding

    # -------------------------------------------------------------- delete

    def delete(self, actor_id: int, node_id: int) -> int:
        """Delete a node and everything under it. Returns the subtree size.

        The count is the size of the subtree **at the moment it was counted**,
        under the lock described below, and that is not quite the same as the
        number of rows the cascade went on to remove: a move *out* of the
        subtree committing in between leaves the count high by what escaped.
        The comment on step 2 says why closing that costs more than it is
        worth. "How many were removed" was the old wording and promised a
        number this cannot give.

        The subtree goes through `ON DELETE CASCADE` (I3), so one statement
        removes an arbitrary number of rows. Two consequences the caller feels:

        * The count has to be taken **before** the delete, because afterwards
          there is nothing left to count and the rowcount sees only the one row
          the statement named.
        * A large subtree can exceed `statement_timeout`, and it will have held
          locks on every row for the whole budget first. That arrives as
          `57014`, which `_with_retry` maps to 503 and deliberately does not
          retry -- a delete that exceeded the timeout will exceed it again,
          and the second attempt pays the same cost before failing the same
          way.
        """
        return self._with_retry("delete", node_id, lambda: self._delete_once(actor_id, node_id))

    def _delete_once(self, actor_id: int, node_id: int) -> int:
        # 1. Resolve first, unlocked, so a foreign id never waits on a
        #    stranger's transaction. Same reasoning as `move` and `create`.
        if self._repo.get(actor_id, node_id) is None:
            raise NodeNotFound

        # 2. Lock the node before counting.
        #
        #    One row is enough. `create` locks its parent's entire ancestry, so
        #    a create anywhere beneath this node already conflicts here and the
        #    subtree cannot grow between the count and the delete. A move INTO
        #    the subtree locks the destination chain, which also includes this
        #    node.
        #
        #    A move OUT of the subtree is the one case this does not serialise:
        #    it locks the node it moves and the destination chain, neither of
        #    which need include this node. If it commits between the count and
        #    the delete, the escaped subtree survives -- correctly -- and the
        #    count is high by its size. The alternative is locking every row in
        #    the subtree, which is the cost this design exists to avoid.
        if not self._repo.lock_one(actor_id, node_id):
            raise NodeNotFound  # deleted between the read and the lock

        # 3. Count while holding the lock.
        removed = self._repo.subtree_size(actor_id, node_id)

        # 4. Delete the root of the subtree; the cascade does the rest.
        if self._repo.delete_node(actor_id, node_id) == 0:
            raise NodeNotFound

        return removed

    # --------------------------------------------------------------- lists

    def list_children(
        self, actor_id: int, parent_id: int, limit: int = DEFAULT_PAGE, after: Cursor = None
    ) -> Page:
        """One page of a folder's contents.

        `after` is the `(name, id)` of the last row the caller saw; None starts
        at the beginning. The returned cursor is None on the last page.

        A missing or foreign `parent_id` is a 404, not an empty page. An empty
        page would make "this folder has nothing in it" and "this folder is not
        yours" the same answer, and the second one is not the caller's to know.

        Wrapped in `_with_retry` for the **error mapping**, not the retrying: a
        plain SELECT takes no row locks, so `40P01` and `55P03` are not
        reachable from here. What is reachable is `57014` on a slow page and
        any other driver error, and those must not cross this boundary carrying
        their statement.
        """
        return self._with_retry(
            "list_children",
            parent_id,
            lambda: self._list_children_once(actor_id, parent_id, limit, after),
        )

    def _list_children_once(self, actor_id: int, parent_id: int, limit: int, after: Cursor) -> Page:
        # Inside the wrapper, not before it. A `get` outside would escape as a
        # raw driver error on any database failure, which is the leak the
        # rest of this class was just fixed for.
        parent = self._repo.get(actor_id, parent_id)
        if parent is None:
            raise NodeNotFound
        if parent.type == "file":
            # I5 says a file is always a leaf, so "the children of a file" is
            # not a question with an answer. `create` already refuses it; an
            # empty page here would quietly accept the same mistake.
            raise FileAsParent
        rows = self._repo.list_children(actor_id, parent_id, _fetch(limit), _start(after))
        return self._page(rows, limit)

    def list_roots(self, actor_id: int, limit: int = DEFAULT_PAGE, after: Cursor = None) -> Page:
        """One page of the caller's roots. No parent to check, so no 404.

        Same note as `list_children` on why this is wrapped.
        """
        return self._with_retry(
            "list_roots",
            None,
            lambda: self._page(
                self._repo.list_roots(actor_id, _fetch(limit), _start(after)), limit
            ),
        )

    @staticmethod
    def _page(rows: list[NodeRow], limit: int) -> Page:
        """Trim the lookahead row and turn the last one into a cursor.

        One row more than the page size is fetched, and its presence is the only
        evidence that another page exists. Returning a cursor whenever the page
        came back full would hand the caller a cursor that leads to nothing --
        a trailing empty page on every list whose size happens to divide evenly.
        """
        wanted = _clamp(limit)
        has_more = len(rows) > wanted
        rows = rows[:wanted]
        # Annotated rather than inferred: a dict literal of mixed value types
        # infers dict[str, str | int | None], and dict's value type is
        # invariant, so that is not a dict[str, object].
        items: list[dict[str, object]] = [
            {"id": r.id, "parent_id": r.parent_id, "name": r.name, "type": r.type} for r in rows
        ]
        cursor = (rows[-1].name, rows[-1].id) if has_more and rows else None
        return items, cursor

    # ---------------------------------------------------------------- reads

    def get(self, actor_id: int, node_id: int) -> dict[str, object]:
        """One node as a plain dict, or `NodeNotFound`.

        Exists so the HTTP layer can build a response body without reaching
        into `_repo`. A route that touches a private is a route that will still
        be touching it when the private changes.

        Wrapped like every sibling. Unwrapped it was the odd one out: a
        statement timeout here escaped as a raw driver error and a 500, where
        the same timeout one method along is a mapped 503.
        """
        return self._with_retry("get", node_id, lambda: self._get_once(actor_id, node_id))

    def _get_once(self, actor_id: int, node_id: int) -> dict[str, object]:
        row = self._repo.get(actor_id, node_id)
        if row is None:
            raise NodeNotFound
        return {"id": row.id, "parent_id": row.parent_id, "name": row.name, "type": row.type}

    def path(self, actor_id: int, node_id: int) -> list[dict[str, object]]:
        """Breadcrumbs, root first.

        The underlying walk filters by owner in its recursive term as well as
        its anchor. Filtering only the anchor would let this climb out of the
        caller's tree and return a stranger's folder names.

        Wrapped for the error mapping, like the listings: a timeout on a deep
        walk is a 503, not a raw driver error escaping as a 500.
        """
        return self._with_retry("path", node_id, lambda: self._path_once(actor_id, node_id))

    def _path_once(self, actor_id: int, node_id: int) -> list[dict[str, object]]:
        rows = self._repo.ancestry(actor_id, node_id)
        if not rows:
            raise NodeNotFound
        return [{"id": r[0], "name": r[2], "type": r[3]} for r in sorted(rows, key=lambda r: -r[4])]

    # ---------------------------------------------------------------- errors

    @staticmethod
    def _translate(exc: IntegrityError) -> Exception:
        """Map a constraint violation to a domain error, by name.

        By name rather than by message text: messages are not stable across
        PostgreSQL versions or locales, and matching on them is how error
        handling silently stops working after an upgrade.
        """
        name = _constraint_of(exc)
        if name in ("uq_nodes_parent_name", "uq_nodes_root_name"):
            return DuplicateName()
        if name == "ck_nodes_not_own_parent":
            return CycleDetected()
        if name == "fk_nodes_parent_same_owner":
            # Should be unreachable: ownership is checked twice above. If it
            # fires, the database caught something the service missed, which is
            # the whole reason the composite key exists.
            log.error("composite owner key rejected a move the service allowed")
            return NodeNotFound()
        if name == "ck_nodes_name_not_blank":
            return InvalidName()
        # Anything unmapped. The detail is logged and NOT returned: str() on an
        # IntegrityError embeds the statement, the bound parameters and the
        # constraint, which together name the owner and whatever the user
        # submitted.
        log.error(
            "unmapped constraint violation",
            extra={"constraint": name or "<none reported>"},
        )
        return StorageError()
