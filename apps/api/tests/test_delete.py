"""`delete`, and the two things that make it more than a DELETE statement.

I3 says deleting a node deletes its whole subtree. The schema does that with
`ON DELETE CASCADE`, so the service issues one statement and an arbitrary
number of rows disappear. That is the right design and it has two edges:

* the count has to be taken **before** the delete, because the rowcount sees
  only the row the statement named and afterwards there is nothing to count;
* a large subtree can exceed `statement_timeout` having already held locks on
  every row for the whole budget, which arrives as `57014`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from file_explorer.errors import NodeNotFound, OperationTimedOut, TreeTooDeep
from file_explorer.services.tree import TreeService

MAX_DEPTH = 64


@pytest.fixture
def svc(session: Session) -> TreeService:
    return TreeService(session, MAX_DEPTH)


def _count(session: Session) -> int:
    return int(session.execute(text("SELECT count(*) FROM nodes")).scalar_one())


def _exists(session: Session, node_id: int) -> bool:
    return (
        session.execute(text("SELECT 1 FROM nodes WHERE id = :i"), {"i": node_id}).one_or_none()
        is not None
    )


# ---------------------------------------------------------------------- I3


@pytest.mark.invariant
def test_deleting_a_folder_takes_its_whole_subtree(
    svc: TreeService, session: Session, alice: int
) -> None:
    """I3. The cascade, not the ORM.

    The original schema had no cascade, so SQLAlchemy nulled each child's
    `parent_id` instead — and NULL means root, so deleting a folder emptied it
    onto the top level rather than removing it.
    """
    home = svc.create(alice, None, "home", "folder")
    docs = svc.create(alice, home, "docs", "folder")
    deep = svc.create(alice, docs, "deep", "folder")
    svc.create(alice, deep, "leaf.txt", "file")
    session.commit()

    svc.delete(alice, home)
    session.commit()

    assert _count(session) == 0, "the subtree survived the delete"


@pytest.mark.invariant
def test_a_deleted_subtree_does_not_reappear_at_the_root(
    svc: TreeService, session: Session, alice: int
) -> None:
    """The specific shape of the original bug, asserted directly.

    Counting rows would pass against an implementation that re-parented the
    children to NULL and then deleted only the folder. This does not.
    """
    home = svc.create(alice, None, "home", "folder")
    child = svc.create(alice, home, "child", "folder")
    session.commit()

    svc.delete(alice, home)
    session.commit()

    orphans = session.execute(
        text("SELECT count(*) FROM nodes WHERE parent_id IS NULL")
    ).scalar_one()
    assert orphans == 0, "a child was re-parented to the root instead of deleted"
    assert not _exists(session, child)


@pytest.mark.invariant
def test_siblings_of_the_deleted_node_survive(
    svc: TreeService, session: Session, alice: int
) -> None:
    """The cascade must follow the edges, not the owner."""
    home = svc.create(alice, None, "home", "folder")
    doomed = svc.create(alice, home, "doomed", "folder")
    svc.create(alice, doomed, "inside", "file")
    keep = svc.create(alice, home, "keep", "folder")
    session.commit()

    svc.delete(alice, doomed)
    session.commit()

    assert _exists(session, home)
    assert _exists(session, keep)
    assert _count(session) == 2


# ------------------------------------------------------------------- count


@pytest.mark.invariant
def test_the_count_includes_the_node_itself(svc: TreeService, alice: int) -> None:
    assert svc.delete(alice, svc.create(alice, None, "lonely", "file")) == 1


@pytest.mark.invariant
def test_the_count_is_the_whole_subtree(svc: TreeService, session: Session, alice: int) -> None:
    home = svc.create(alice, None, "home", "folder")
    docs = svc.create(alice, home, "docs", "folder")
    svc.create(alice, docs, "a.txt", "file")
    svc.create(alice, docs, "b.txt", "file")
    svc.create(alice, home, "c.txt", "file")
    session.commit()

    assert svc.delete(alice, home) == 5


@pytest.mark.invariant
def test_the_count_stops_at_the_owner_boundary(
    svc: TreeService,
    session: Session,
    alice: int,
    bob: int,
    make_node: Callable[..., int],
    without_owner_fk: None,
) -> None:
    """The counting walk filters by owner in its recursive term too.

    The composite foreign key makes this state unreachable through the API, so
    it is built by dropping the key — the walk has to be right on its own
    terms, not because something upstream is.
    """
    home = make_node(alice, "home")
    intruder = make_node(bob, "theirs", parent_id=home)
    session.commit()

    assert svc._repo.subtree_size(alice, home) == 1, (
        "the count crossed an owner boundary and included another user's node"
    )
    assert _exists(session, intruder)


@pytest.mark.invariant
def test_an_over_deep_subtree_is_not_counted_short(
    session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """An undercount would be reported as the number of things deleted.

    That is a lie about data that is already gone, which is why the bounded
    walk raises instead of returning what it managed to reach.
    """
    svc = TreeService(session, 4)
    parent: int | None = None
    for level in range(7):
        parent = make_node(alice, f"L{level}", parent_id=parent)
    root = int(session.execute(text("SELECT id FROM nodes WHERE parent_id IS NULL")).scalar_one())
    session.commit()

    with pytest.raises(TreeTooDeep):
        svc.delete(alice, root)


# -------------------------------------------------------------- ownership


@pytest.mark.invariant
def test_deleting_a_foreign_node_is_404_and_changes_nothing(
    svc: TreeService, session: Session, alice: int, bob: int, make_node: Callable[..., int]
) -> None:
    theirs = make_node(bob, "bob-private")
    make_node(bob, "bob-child", parent_id=theirs)
    session.commit()

    with pytest.raises(NodeNotFound):
        svc.delete(alice, theirs)
    session.rollback()

    assert _count(session) == 2, "a foreign subtree was deleted"


@pytest.mark.invariant
def test_deleting_a_missing_node_is_404(svc: TreeService, alice: int) -> None:
    with pytest.raises(NodeNotFound):
        svc.delete(alice, 999999)


# ---------------------------------------------------------------- timeouts


@pytest.mark.concurrency
def test_a_timed_out_delete_is_503_and_is_not_retried(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[..., Session],
) -> None:
    """57014 on the delete step maps to 503, and is not retried.

    The realistic cause is a cascade over a large subtree, which can exceed
    `statement_timeout` having already held locks on every row for the whole
    budget. Retrying spends the budget again and fails the same way, so it is
    mapped rather than retried.

    Two things this test does **not** do, having been corrected on both:

    * It no longer sets a 1 ms timeout and hopes. A single-row indexed SELECT
      can finish inside a millisecond on a warm connection, so the old version
      was flaky by construction — and when it did fire, it fired on `get()`,
      the first statement, so the delete path was never reached at all.
    * It does not try to build a subtree genuinely large enough to blow a
      timeout. That is just the same race with a bigger setup cost.

    Instead the sqlstate is produced deterministically at the point the real
    one would occur: a slow statement wrapped around `delete_node`. The code
    under test — the mapping, and that this path does not retry — is exercised
    for real.
    """
    home = make_node(alice, "home")
    make_node(alice, "child", parent_id=home)
    session.commit()

    actor = new_session(statement_timeout_ms=100)
    svc = TreeService(actor, MAX_DEPTH)
    original = svc._repo.delete_node
    calls: list[int] = []

    def slow_delete(owner_id: int, node_id: int) -> int:
        calls.append(node_id)
        actor.execute(text("SELECT pg_sleep(0.5)"))  # exceeds the 100 ms budget
        return original(owner_id, node_id)

    svc._repo.delete_node = slow_delete  # type: ignore[method-assign]

    with pytest.raises(OperationTimedOut):
        svc.delete(alice, home)
    actor.rollback()

    assert calls == [home], f"the timeout did not fire on the delete step: {calls}"
    session.rollback()
    assert _exists(session, home), "the node was removed despite the timeout"
    assert _count(session) == 2, "the cascade ran despite the timeout"


@pytest.mark.concurrency
def test_the_count_cannot_miss_a_create_that_was_in_flight(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[..., Session],
) -> None:
    """`delete` must take its lock BEFORE counting, and the count is what proves it.

    The first version of this test asserted that a create beneath the doomed
    node blocks while the node is locked — by calling `lock_one` directly. That
    passed with the lock removed from `delete` entirely, because it never went
    through `delete` at all. Mutation testing caught it; it is the third test in
    this project that could not fail for its stated reason.

    What distinguishes the two orderings is the number `delete` returns:

    * **lock, then count** — `delete` blocks on the lock before it counts, so
      when it wakes the in-flight create has committed and the count sees all
      three nodes.
    * **count, then lock** — the count is a plain read and does not block, so it
      sees two. `delete` then blocks on the DELETE, wakes, and the cascade
      removes all three. It reports two and destroys three.

    Both orderings block, and both end with an empty table. Only the returned
    count tells them apart.
    """
    home = make_node(alice, "home")
    inner = make_node(alice, "inner", parent_id=home)
    session.commit()

    holder = new_session()
    actor = new_session(lock_timeout_ms=5000)

    # In flight: created, holding FOR NO KEY UPDATE on {inner, home}, uncommitted.
    TreeService(holder, MAX_DEPTH).create(alice, inner, "sneaky", "file")

    reported: dict[str, object] = {}
    done = threading.Event()

    def attempt() -> None:
        try:
            reported["count"] = TreeService(actor, MAX_DEPTH).delete(alice, home)
            actor.commit()
        except Exception as exc:
            actor.rollback()
            reported["error"] = type(exc).__name__
        done.set()

    thread = threading.Thread(target=attempt, daemon=True)
    thread.start()

    assert not done.wait(timeout=0.5), (
        "delete did not block while a create held the chain beneath it"
    )

    holder.commit()
    assert done.wait(timeout=10), "delete never finished after the create committed"
    thread.join(timeout=5)

    assert "error" not in reported, f"delete failed: {reported}"
    assert reported["count"] == 3, (
        f"delete reported {reported['count']} nodes but removed 3 — it counted the "
        "subtree before taking its lock, so a create that was in flight was "
        "destroyed without ever being counted"
    )

    session.rollback()
    assert _count(session) == 0, "the subtree survived"
