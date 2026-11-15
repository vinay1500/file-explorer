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
def test_a_delete_that_exceeds_the_timeout_is_503_and_is_not_retried(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[..., Session],
) -> None:
    """57014, the reason this operation needed handling at all.

    A cascading delete of a large subtree can exceed `statement_timeout` having
    already held locks on every row for the whole budget. Retrying spends the
    budget a second time and fails the same way, so it is mapped to 503 rather
    than retried.
    """
    home = make_node(alice, "home")
    session.commit()

    # One millisecond: the first statement the delete issues already exceeds it.
    actor = new_session(statement_timeout_ms=1)
    with pytest.raises(OperationTimedOut):
        TreeService(actor, MAX_DEPTH).delete(alice, home)
    actor.rollback()

    assert _exists(session, home), "the node was removed despite the timeout"


@pytest.mark.concurrency
def test_a_create_cannot_slip_into_a_subtree_being_deleted(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[..., Session],
) -> None:
    """The lock the delete holds over its count.

    `create` locks its parent's entire ancestry, so a create anywhere beneath
    the doomed node conflicts with the single row the delete locks. If it did
    not, a node could be inserted between the count and the delete — and it
    would then be removed by the cascade without ever being counted.
    """
    home = make_node(alice, "home")
    inner = make_node(alice, "inner", parent_id=home)
    session.commit()

    deleter = new_session()
    actor = new_session(lock_timeout_ms=3000)

    # Take the lock the delete takes, then hold it.
    assert TreeService(deleter, MAX_DEPTH)._repo.lock_one(alice, home)

    done = threading.Event()
    outcome: dict[str, object] = {}

    def attempt() -> None:
        try:
            TreeService(actor, MAX_DEPTH).create(alice, inner, "sneaky", "file")
            actor.commit()
            outcome["result"] = "created"
        except Exception as exc:
            actor.rollback()
            outcome["result"] = type(exc).__name__
        done.set()

    thread = threading.Thread(target=attempt, daemon=True)
    thread.start()

    assert not done.wait(timeout=0.5), (
        "a create beneath the doomed node did not block, so it could be inserted "
        "between the delete's count and the delete itself"
    )

    deleter.rollback()
    assert done.wait(timeout=10), "the create never finished after the lock was released"
    thread.join(timeout=5)
    assert outcome.get("result") == "created", f"unexpected outcome: {outcome}"
