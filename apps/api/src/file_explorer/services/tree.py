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

from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from file_explorer.errors import (
    CycleDetected,
    DepthExceeded,
    DuplicateName,
    FileAsParent,
    InvalidName,
    NodeNotFound,
    OperationTimedOut,
    StorageError,
)
from file_explorer.repositories.node_repo import NodeRepository

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


def _constraint_of(exc: IntegrityError) -> str:
    """The constraint a violation names, or "" if the driver did not say."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None) or ""


class TreeService:
    def __init__(self, session: Session, max_depth: int) -> None:
        self._s = session
        self._repo = NodeRepository(session, max_depth)
        self._max_depth = max_depth

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
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                self._move_once(actor_id, node_id, new_parent_id)
                return
            except DBAPIError as exc:
                sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
                if sqlstate == TIMEOUT:
                    self._s.rollback()
                    log.warning("statement timed out", extra={"node_id": node_id})
                    raise OperationTimedOut from exc
                if sqlstate not in RETRYABLE:
                    raise
                self._s.rollback()
                if attempt == _MAX_ATTEMPTS:
                    raise
                log.warning(
                    "contention on move, retrying",
                    extra={"attempt": attempt, "node_id": node_id, "sqlstate": sqlstate},
                )
                # Linear backoff with jitter. Without jitter two transactions
                # retrying in step keep colliding on the same schedule.
                time.sleep(0.02 * attempt + random.uniform(0, 0.02))

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
        dest_level = (
            0 if new_parent_id is None else len(self._repo.ancestry(actor_id, new_parent_id))
        )
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

    # ---------------------------------------------------------------- reads

    def path(self, actor_id: int, node_id: int) -> list[dict[str, object]]:
        """Breadcrumbs, root first.

        The underlying walk filters by owner in its recursive term as well as
        its anchor. Filtering only the anchor would let this climb out of the
        caller's tree and return a stranger's folder names.
        """
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
