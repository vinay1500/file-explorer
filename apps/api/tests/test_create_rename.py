"""`create` and `rename` — the two writes that were still missing.

The interesting one is `create`. It looks like an insert with a parent check,
and most of it is; what makes it worth a test file is the depth cap, which is
not really about I7 at all. Both recursive walks stop at `max_depth`, so a tree
that is one level over the cap makes `ancestry` return a **partial** chain with
no error — and a partial ancestry makes the cycle check fail open. The cap is
load-bearing for I1.

Which means the measurement has to be taken under a lock, exactly like `move`
does, or a concurrent move can deepen the chain underneath it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from file_explorer.errors import (
    DepthExceeded,
    DuplicateName,
    FileAsParent,
    InvalidName,
    InvalidType,
    NodeNotFound,
    TreeBusy,
    TreeTooDeep,
)
from file_explorer.services.tree import TreeService

MAX_DEPTH = 64


def _wait_until(predicate: Callable[[], bool], seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def svc(session: Session) -> TreeService:
    return TreeService(session, MAX_DEPTH)


def _row(session: Session, node_id: int) -> tuple[int, int | None, str, str]:
    r = session.execute(
        text("SELECT owner_id, parent_id, name, type::text FROM nodes WHERE id = :i"),
        {"i": node_id},
    ).one()
    return (r[0], r[1], r[2], r[3])


# ------------------------------------------------------------------ create


@pytest.mark.invariant
def test_create_at_the_root(svc: TreeService, session: Session, alice: int) -> None:
    node_id = svc.create(alice, None, "home", "folder")
    owner, parent, name, kind = _row(session, node_id)
    assert (owner, parent, name, kind) == (alice, None, "home", "folder")


@pytest.mark.invariant
def test_create_under_a_folder(svc: TreeService, session: Session, alice: int) -> None:
    home = svc.create(alice, None, "home", "folder")
    doc = svc.create(alice, home, "notes.txt", "file")
    assert _row(session, doc)[1] == home


@pytest.mark.invariant
def test_the_owner_is_the_actor_not_the_parent(
    svc: TreeService, session: Session, alice: int
) -> None:
    """`owner_id` is written from the actor, never carried over from input.

    The composite key refuses a parent belonging to anyone else, so this is
    belt and braces — but the guard has to hold on its own terms.
    """
    home = svc.create(alice, None, "home", "folder")
    assert _row(session, svc.create(alice, home, "child", "folder"))[0] == alice


# ---- I5 -------------------------------------------------------------------


@pytest.mark.invariant
def test_a_file_cannot_be_given_children(svc: TreeService, alice: int) -> None:
    """I5. A file is always a leaf."""
    doc = svc.create(alice, None, "notes.txt", "file")
    with pytest.raises(FileAsParent):
        svc.create(alice, doc, "child", "folder")


# ---- ownership ------------------------------------------------------------


@pytest.mark.invariant
def test_creating_under_a_foreign_parent_is_404(
    svc: TreeService, session: Session, alice: int, bob: int, make_node: Callable[..., int]
) -> None:
    """404, never 403. Node ids are sequential, so 403 would enumerate them."""
    theirs = make_node(bob, "bob-private")
    session.commit()
    with pytest.raises(NodeNotFound):
        svc.create(alice, theirs, "mine", "folder")


@pytest.mark.invariant
def test_creating_under_a_missing_parent_is_404(svc: TreeService, alice: int) -> None:
    with pytest.raises(NodeNotFound):
        svc.create(alice, 999999, "orphan", "folder")


# ---- I2 and I4 ------------------------------------------------------------


@pytest.mark.invariant
def test_duplicate_sibling_name_is_rejected(svc: TreeService, alice: int) -> None:
    """I4. Enforced by `uq_nodes_parent_name`, not by a pre-check.

    Nothing locks the parent's existing children, so a pre-check could be
    overtaken by a rename between the check and the insert.
    """
    home = svc.create(alice, None, "home", "folder")
    svc.create(alice, home, "report", "file")
    with pytest.raises(DuplicateName):
        svc.create(alice, home, "report", "file")


@pytest.mark.invariant
def test_duplicate_root_name_is_rejected(svc: TreeService, alice: int) -> None:
    """I2. Roots are scoped per owner by a partial unique index.

    Partial because PostgreSQL treats NULLs as distinct, so a plain
    UNIQUE (parent_id, name) would not constrain roots at all.
    """
    svc.create(alice, None, "home", "folder")
    with pytest.raises(DuplicateName):
        svc.create(alice, None, "home", "folder")


@pytest.mark.invariant
def test_two_owners_may_each_have_a_root_called_home(
    svc: TreeService, session: Session, alice: int, bob: int
) -> None:
    svc.create(alice, None, "home", "folder")
    svc.create(bob, None, "home", "folder")
    count = session.execute(text("SELECT count(*) FROM nodes WHERE name = 'home'")).scalar_one()
    assert count == 2


@pytest.mark.invariant
def test_the_same_name_may_appear_in_different_folders(svc: TreeService, alice: int) -> None:
    a = svc.create(alice, None, "a", "folder")
    b = svc.create(alice, None, "b", "folder")
    svc.create(alice, a, "report", "file")
    svc.create(alice, b, "report", "file")  # must not raise


# ---- I7, and why it is really about I1 ------------------------------------


@pytest.mark.invariant
def test_a_chain_may_reach_the_cap_but_not_pass_it(session: Session, alice: int) -> None:
    """I7, at the exact boundary rather than somewhere near it.

    A cap tested only well inside its limit is a cap that can be off by one
    forever. `max_depth` here is 4, so the whole boundary fits in a test.
    """
    svc = TreeService(session, 4)

    level1 = svc.create(alice, None, "L1", "folder")
    level2 = svc.create(alice, level1, "L2", "folder")
    level3 = svc.create(alice, level2, "L3", "folder")
    level4 = svc.create(alice, level3, "L4", "folder")  # at the cap, allowed

    with pytest.raises(DepthExceeded):
        svc.create(alice, level4, "L5", "folder")


@pytest.mark.invariant
def test_an_over_deep_chain_is_detected_not_silently_truncated(
    session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """The reason the cap matters.

    Built with raw inserts, which is the realistic way this state arrives: a
    migration, a manual fix, or a cap that was raised and then lowered. The
    walk must say so rather than return the first `max_depth` rows as if they
    were the whole chain — a truncated ancestry is what makes `is_ancestor_of`
    miss a real ancestor and the cycle check fail open.
    """
    svc = TreeService(session, 4)

    parent: int | None = None
    deepest = 0
    for level in range(6):  # 6 levels against a cap of 4
        parent = make_node(alice, f"L{level}", parent_id=parent)
        deepest = parent
    session.commit()

    with pytest.raises(TreeTooDeep):
        svc.path(alice, deepest)


@pytest.mark.invariant
def test_an_over_deep_subtree_is_detected_from_below(
    session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """The descending walk truncates too, and its floor would be used as a height."""
    svc = TreeService(session, 4)

    root = make_node(alice, "L0")
    parent = root
    for level in range(1, 7):
        parent = make_node(alice, f"L{level}", parent_id=parent)
    session.commit()

    with pytest.raises(TreeTooDeep):
        svc._repo.subtree_height(alice, root)


# ---- the payload on its own terms -----------------------------------------


@pytest.mark.invariant
def test_an_unknown_type_is_422(svc: TreeService, alice: int) -> None:
    """Checked in the service, because the enum would raise the wrong class.

    A bad value reaches PostgreSQL as `invalid input value for enum node_type`
    — a `DataError`, not an `IntegrityError` — so it would miss `_translate`
    and escape as a driver exception carrying the statement.
    """
    with pytest.raises(InvalidType):
        svc.create(alice, None, "thing", "directory")


@pytest.mark.invariant
@pytest.mark.parametrize("name", ["", " ", "\t", "\n", " \t\r\n "])
def test_a_blank_name_is_422_not_409(svc: TreeService, alice: int, name: str) -> None:
    """`ck_nodes_name_not_blank`, mapped to 422 rather than "already used".

    `btrim` with one argument trims spaces only, so the tab case is the one
    that used to pass.
    """
    with pytest.raises(InvalidName):
        svc.create(alice, None, name, "folder")


# ------------------------------------------------------------------ rename


@pytest.mark.invariant
def test_rename_changes_the_name(svc: TreeService, session: Session, alice: int) -> None:
    node_id = svc.create(alice, None, "old", "folder")
    svc.rename(alice, node_id, "new")
    assert _row(session, node_id)[2] == "new"


@pytest.mark.invariant
def test_rename_onto_a_sibling_name_is_409(svc: TreeService, alice: int) -> None:
    home = svc.create(alice, None, "home", "folder")
    svc.create(alice, home, "taken", "file")
    other = svc.create(alice, home, "free", "file")
    with pytest.raises(DuplicateName):
        svc.rename(alice, other, "taken")


@pytest.mark.invariant
def test_renaming_to_the_current_name_is_a_noop(
    svc: TreeService, session: Session, alice: int
) -> None:
    """Idempotent, and specifically it does not bump `updated_at`.

    Renaming a row to its own name does not violate the unique index — it is
    the same row — so this is a deliberate choice rather than a workaround.
    """
    node_id = svc.create(alice, None, "same", "folder")
    session.commit()
    before = session.execute(
        text("SELECT updated_at FROM nodes WHERE id = :i"), {"i": node_id}
    ).scalar_one()

    svc.rename(alice, node_id, "same")
    session.commit()

    after = session.execute(
        text("SELECT updated_at FROM nodes WHERE id = :i"), {"i": node_id}
    ).scalar_one()
    assert before == after


@pytest.mark.invariant
def test_renaming_a_foreign_node_is_404(
    svc: TreeService, session: Session, alice: int, bob: int, make_node: Callable[..., int]
) -> None:
    theirs = make_node(bob, "bob-private")
    session.commit()
    with pytest.raises(NodeNotFound):
        svc.rename(alice, theirs, "mine-now")
    assert _row(session, theirs)[2] == "bob-private"


@pytest.mark.invariant
def test_renaming_a_missing_node_is_404(svc: TreeService, alice: int) -> None:
    with pytest.raises(NodeNotFound):
        svc.rename(alice, 999999, "ghost")


@pytest.mark.invariant
def test_renaming_to_blank_is_422(svc: TreeService, alice: int) -> None:
    node_id = svc.create(alice, None, "fine", "folder")
    with pytest.raises(InvalidName):
        svc.rename(alice, node_id, "\t")


# ------------------------------------------------------- create vs. move


@pytest.mark.concurrency
def test_create_waits_for_a_move_holding_the_parent_chain(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[..., Session],
) -> None:
    """The lock is the whole point of taking it.

    If `create` measured depth without locking the parent's chain, a move could
    re-parent an ancestor between the measurement and the insert and the new
    node would land past the cap. This asserts the create genuinely **blocks**
    while the chain is held — if it did not, the serialisation argument would
    be wrong and nothing else here would catch it.
    """
    top = make_node(alice, "top")
    middle = make_node(alice, "middle", parent_id=top)
    session.commit()

    holder = new_session()
    actor = new_session(lock_timeout_ms=3000)

    # Hold the chain above `middle`, exactly as a move of `middle` would.
    TreeService(holder, MAX_DEPTH)._repo.lock_ancestry(alice, middle)

    done = threading.Event()
    outcome: dict[str, object] = {}

    def attempt() -> None:
        try:
            outcome["id"] = TreeService(actor, MAX_DEPTH).create(alice, middle, "new", "folder")
            actor.commit()
        except Exception as exc:
            actor.rollback()
            outcome["error"] = type(exc).__name__
        done.set()

    thread = threading.Thread(target=attempt, daemon=True)
    thread.start()

    assert not done.wait(timeout=0.5), (
        "create did not block while the parent chain was locked, so its depth "
        "measurement is not serialised against a concurrent move"
    )

    holder.rollback()
    assert done.wait(timeout=10), "create never finished after the lock was released"
    thread.join(timeout=5)

    assert "error" not in outcome, f"create failed after the lock was released: {outcome}"
    assert (
        session.execute(
            text("SELECT parent_id FROM nodes WHERE id = :i"), {"i": outcome["id"]}
        ).scalar_one()
        == middle
    )


@pytest.mark.concurrency
def test_create_does_not_block_an_unrelated_create(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[..., Session],
) -> None:
    """Chains that do not overlap must not serialise against each other.

    A lock that is correct but too broad turns every create in the tree into a
    queue. Two separate roots share no ancestor, so neither should wait.
    """
    left = make_node(alice, "left")
    right = make_node(alice, "right")
    session.commit()

    holder = new_session()
    actor = new_session(lock_timeout_ms=1000)

    TreeService(holder, MAX_DEPTH)._repo.lock_ancestry(alice, left)
    try:
        started = time.monotonic()
        node_id = TreeService(actor, MAX_DEPTH).create(alice, right, "free", "folder")
        actor.commit()
        elapsed = time.monotonic() - started
    finally:
        holder.rollback()

    assert node_id, "the unrelated create should have succeeded"
    assert elapsed < 0.9, f"the unrelated create waited {elapsed:.2f}s on an unrelated lock"


# ------------------------------------------- the lock set vs the measurement


@pytest.mark.concurrency
def test_no_node_can_be_created_past_the_cap_while_the_chain_moves(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[..., Session],
) -> None:
    """Gate 3, BLOCK. The lock set and the measurement come from two snapshots.

    `lock_ancestry` decides *which* rows to lock from a recursive CTE evaluated
    under its own snapshot, and `LockRows` sits above that plan — it does not
    re-run the CTE after a lock wait. (The same fact chapter 10 records for
    `move`, which is why step 3 there must be a fresh statement.)

    So if an ancestor is re-parented while `create` is waiting for its lock,
    `create` wakes holding locks on a chain that no longer exists, then measures
    a *fresh* chain that is longer. Nothing checks that what it locked covers
    what it measured, and the comment claims otherwise:

        "The lock is what makes the measurement mean anything."

    The interleaving below leaves a node one level past the cap, and the PR's
    own truncation guards then turn that into a 500 on `path()` and on
    `delete()` of any ancestor — so the owner cannot clear it.

    `insert_node` is patched to pause between the measurement and the insert.
    That is a test seam, not a production change: it removes the only timing
    dependence that could not be made deterministic, and the race is reachable
    without it.
    """
    cap = 6
    # R -> P, and two spare two-deep chains to graft onto.
    r = make_node(alice, "R")
    p = make_node(alice, "P", parent_id=r)
    d1 = make_node(alice, "D1")
    d2 = make_node(alice, "D2", parent_id=d1)
    e1 = make_node(alice, "E1")
    e2 = make_node(alice, "E2", parent_id=e1)
    session.commit()

    holder = new_session()
    mover = new_session(lock_timeout_ms=10000)
    creator = new_session(lock_timeout_ms=10000)
    grafter = new_session(lock_timeout_ms=1500)

    def blocked() -> int:
        # pg_locks, not pg_stat_activity: the latter is snapshot-cached inside a
        # transaction (`stats_fetch_consistency`), so polling it from a session
        # that never ends its transaction returns the same answer forever.
        # Rolling back first makes each poll a fresh read either way.
        session.rollback()
        return int(
            session.execute(text("SELECT count(*) FROM pg_locks WHERE NOT granted")).scalar_one()
        )

    # T3 holds R, so the two transactions behind it queue in a known order.
    assert TreeService(holder, cap)._repo.lock_one(alice, r)

    measured = threading.Event()
    proceed = threading.Event()
    outcome: dict[str, object] = {}

    # T2: a legal move that makes P deeper. 2 + 1 + 1 = 4 <= 6.
    def do_move() -> None:
        try:
            TreeService(mover, cap).move(alice, r, d2)
            mover.commit()
            outcome["move"] = "ok"
        except Exception as exc:
            mover.rollback()
            outcome["move"] = type(exc).__name__

    t_move = threading.Thread(target=do_move, daemon=True)
    t_move.start()
    assert _wait_until(lambda: blocked() >= 1), "the move never queued behind the holder"

    # T1: creates under P. Its CTE snapshot sees only {P, R}.
    def do_create() -> None:
        svc = TreeService(creator, cap)
        original = svc._repo.insert_node

        def paused(*args: object, **kwargs: object) -> int:
            measured.set()  # depth has been measured; let the graft run
            proceed.wait(timeout=15)
            return original(*args, **kwargs)  # type: ignore[arg-type]

        svc._repo.insert_node = paused  # type: ignore[method-assign]
        try:
            svc.create(alice, p, "x", "folder")
            creator.commit()
            outcome["create"] = "ok"
        except Exception as exc:
            creator.rollback()
            outcome["create"] = type(exc).__name__

    t_create = threading.Thread(target=do_create, daemon=True)
    t_create.start()
    assert _wait_until(lambda: blocked() >= 2), "the create never queued behind the move"

    # Release: T2 takes R and commits, then T1 wakes with a stale lock set.
    holder.rollback()

    # T4: another legal move, on a chain T1 did not lock. 2 + 1 + 3 = 6 <= 6.
    measured.wait(timeout=15)
    try:
        TreeService(grafter, cap).move(alice, d1, e2)
        grafter.commit()
        outcome["graft"] = "ok"
    except Exception as exc:
        grafter.rollback()
        outcome["graft"] = type(exc).__name__
    proceed.set()

    t_move.join(timeout=20)
    t_create.join(timeout=20)

    # The assertion is I7 itself, stated against the whole table rather than
    # against whatever this particular interleaving produced.
    session.rollback()
    deepest = session.execute(
        text("""
        WITH RECURSIVE walk AS (
            SELECT id, name, 1 AS level FROM nodes WHERE parent_id IS NULL
            UNION ALL
            SELECT n.id, n.name, w.level + 1
              FROM nodes n JOIN walk w ON n.parent_id = w.id
             WHERE w.level < 100
        )
        SELECT name, level FROM walk ORDER BY level DESC LIMIT 1
        """)
    ).one_or_none()

    assert deepest is not None
    assert deepest[1] <= cap, (
        f"{deepest[0]!r} sits at level {deepest[1]} against a cap of {cap}. "
        f"create locked a chain from one snapshot and measured another. {outcome}"
    )


@pytest.mark.invariant
def test_the_ancestor_check_refuses_to_answer_from_a_truncated_walk(
    session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """Gate 3 MINOR. `is_ancestor_of` was the one bounded walk with no check.

    It returns a boolean, so a truncated walk does not look wrong — it looks
    like "no, not an ancestor" about a node that is one. That is the cycle
    check failing open, on exactly the data the depth bound exists to protect
    it from.

    Nothing caught it because `_move_once` happens to call `lock_move_participants`
    before this, and that walk raises first. Statement ordering is not a guard.
    """
    svc = TreeService(session, 4)

    parent: int | None = None
    for level in range(7):  # 7 levels against a cap of 4
        parent = make_node(alice, f"L{level}", parent_id=parent)
    deepest = parent
    root = int(session.execute(text("SELECT id FROM nodes WHERE parent_id IS NULL")).scalar_one())
    session.commit()

    assert deepest is not None
    with pytest.raises(TreeTooDeep):
        svc._repo.is_ancestor_of(alice, root, deepest)


@pytest.mark.invariant
def test_the_chain_coverage_check_rejects_a_stale_lock_set(
    session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """The guard from the M3a BLOCK, asserted directly rather than raced for.

    The concurrency test that found the bug builds a four-transaction
    interleaving. It is the right test for "can this happen", and it is a poor
    test for "does the check work" — it depends on lock queue order, and a
    change in timing turns it into a test of nothing.

    This drives the condition the check exists for: a lock set that does not
    cover the chain that was then measured. `create` must refuse rather than
    measure a chain it never locked.
    """
    svc = TreeService(session, MAX_DEPTH)
    root = make_node(alice, "root")
    middle = make_node(alice, "middle", parent_id=root)
    session.commit()

    # Exactly what a move committing during the lock wait leaves behind: a set
    # chosen under an older snapshot, missing the part of the chain that moved.
    svc._repo.lock_ancestry = lambda owner_id, node_id: [middle]  # type: ignore[method-assign]

    # The raise is the assertion. A row count after the rollback cannot fail:
    # the guard fires at step 3a, before the insert, and `_with_retry` rolls
    # back on the way out — so "nothing was created" is true however the check
    # behaves, and an assertion that holds either way is not one.
    with pytest.raises(TreeBusy):
        svc.create(alice, middle, "child", "folder")
    session.rollback()


@pytest.mark.invariant
def test_a_chain_that_keeps_shifting_gives_up_rather_than_spinning(
    session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """Bounded, like every other retry here. A loop that never gives up is a hang.

    503 rather than 409: nothing about the request is wrong, the tree was just
    being restructured faster than one attempt could finish.
    """
    svc = TreeService(session, MAX_DEPTH)
    root = make_node(alice, "root")
    middle = make_node(alice, "middle", parent_id=root)
    session.commit()

    attempts: list[int] = []

    def never_covers(owner_id: int, node_id: int) -> list[int]:
        # The parent itself, never its ancestors. An empty list would trip the
        # "deleted between the read and the lock" check first and never reach
        # the coverage comparison this test is about.
        attempts.append(node_id)
        return [middle]

    svc._repo.lock_ancestry = never_covers  # type: ignore[method-assign]

    with pytest.raises(TreeBusy) as caught:
        svc.create(alice, middle, "child", "folder")
    session.rollback()

    assert caught.value.status == 503
    assert len(attempts) == 3, f"expected three attempts before giving up, saw {len(attempts)}"


@pytest.mark.invariant
def test_move_also_refuses_a_stale_lock_set(
    session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """`move` carries the same check as `create`, and had no test for it.

    Coverage found it: the `create` version is covered by the test above, and
    `move`'s — the one the original BLOCK was actually about — was reached by
    nothing. Two copies of a guard where only one is exercised is how the
    unexercised one quietly stops working.
    """
    svc = TreeService(session, MAX_DEPTH)
    root = make_node(alice, "root")
    middle = make_node(alice, "middle", parent_id=root)
    mover = make_node(alice, "mover")
    session.commit()

    original = svc._repo.lock_move_participants

    def partial(owner_id: int, node_id: int, new_parent_id: int | None) -> list[int]:
        full = original(owner_id, node_id, new_parent_id)
        return [i for i in full if i != root]  # the chain moved; root was never locked

    svc._repo.lock_move_participants = partial  # type: ignore[method-assign]

    # Same as above: the guard precedes `set_parent`, so the parent column is
    # unchanged whether or not the check works.
    with pytest.raises(TreeBusy):
        svc.move(alice, mover, middle)
    session.rollback()


@pytest.mark.invariant
def test_deleting_a_node_that_vanishes_mid_operation_is_404(
    session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """The rowcount check, which is the only thing standing between a race and a lie.

    `delete` resolves, locks and counts before issuing the DELETE. If the row
    is gone by then the statement affects nothing, and reporting success for a
    delete that deleted nothing is how `move(999999, 7)` used to answer 200.
    """
    svc = TreeService(session, MAX_DEPTH)
    doomed = make_node(alice, "doomed")
    session.commit()

    original = svc._repo.delete_node
    svc._repo.delete_node = lambda owner_id, node_id: 0  # type: ignore[method-assign]
    try:
        with pytest.raises(NodeNotFound):
            svc.delete(alice, doomed)
    finally:
        svc._repo.delete_node = original  # type: ignore[method-assign]
    session.rollback()
