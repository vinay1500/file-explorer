"""The eight invariants, and the review findings that produced them.

Each test names the invariant or finding it covers. See `docs/ARCHITECTURE.md`
§4 for the invariants and `.state`-held review notes for the finding ids.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from file_explorer.errors import (
    CycleDetected,
    DepthExceeded,
    DuplicateName,
    FileAsParent,
    NodeNotFound,
)
from file_explorer.models import Node
from file_explorer.services.tree import TreeService

MAX_DEPTH = 64


@pytest.fixture
def svc(session: Session) -> TreeService:
    return TreeService(session, MAX_DEPTH)


def _parent_of(session: Session, node_id: int) -> int | None:
    return session.execute(
        text("SELECT parent_id FROM nodes WHERE id = :i"), {"i": node_id}
    ).scalar_one()


def _count(session: Session) -> int:
    return int(session.execute(text("SELECT count(*) FROM nodes")).scalar_one())


def _is_acyclic(session: Session, owner_id: int) -> bool:
    """Every node reaches the root within MAX_DEPTH steps."""
    reachable = int(
        session.execute(
            text("""
            WITH RECURSIVE down AS (
                SELECT id, 0 AS d FROM nodes
                 WHERE owner_id = :o AND parent_id IS NULL
                UNION ALL
                SELECT n.id, down.d + 1 FROM nodes n
                  JOIN down ON n.parent_id = down.id
                 WHERE n.owner_id = :o AND down.d < :m
            )
            SELECT count(DISTINCT id) FROM down
            """),
            {"o": owner_id, "m": MAX_DEPTH},
        ).scalar_one()
    )
    total = int(
        session.execute(
            text("SELECT count(*) FROM nodes WHERE owner_id = :o"), {"o": owner_id}
        ).scalar_one()
    )
    return reachable == total


# ---------------------------------------------------------------- I1 cycles


@pytest.mark.invariant
def test_move_into_direct_child_rejected(
    svc: TreeService, session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """I1. The case the original code allowed: one level down."""
    a = make_node(alice, "docs")
    b = make_node(alice, "work", parent_id=a)
    session.commit()

    with pytest.raises(CycleDetected):
        svc.move(alice, a, b)

    assert _parent_of(session, a) is None
    assert _is_acyclic(session, alice)


@pytest.mark.invariant
def test_move_into_distant_descendant_rejected(
    svc: TreeService, session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """I1. Five levels down — a single-level check would miss this."""
    ids = []
    parent: int | None = None
    for i in range(6):
        parent = make_node(alice, f"level{i}", parent_id=parent)
        ids.append(parent)
    session.commit()

    with pytest.raises(CycleDetected):
        svc.move(alice, ids[0], ids[5])

    assert _parent_of(session, ids[0]) is None
    assert _is_acyclic(session, alice)


@pytest.mark.invariant
def test_self_parent_rejected(
    svc: TreeService, session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """I8. Also a CHECK constraint, so it cannot reach the database."""
    a = make_node(alice, "docs")
    session.commit()

    with pytest.raises(CycleDetected):
        svc.move(alice, a, a)

    with pytest.raises(IntegrityError):
        session.execute(text("UPDATE nodes SET parent_id = :i WHERE id = :i"), {"i": a})
        session.flush()
    session.rollback()


# ------------------------------------------------- move to root (F-06, G-03)


@pytest.mark.invariant
def test_move_to_root_works(
    svc: TreeService, session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """F-06. The original interface could never do this at all."""
    a = make_node(alice, "docs")
    b = make_node(alice, "work", parent_id=a)
    session.commit()

    svc.move(alice, b, None)
    session.commit()

    assert _parent_of(session, b) is None
    assert _count(session) == 2


@pytest.mark.invariant
def test_move_to_root_does_not_delete_subtree(
    svc: TreeService, session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """G-03. The finding that would have shipped.

    `cascade="all, delete-orphan"` deletes a child on de-association, and
    setting `parent` to None *is* de-association — so the ORM would emit a
    DELETE, and `passive_deletes=True` would let PostgreSQL's cascade take the
    whole subtree.

    Reparenting between two loaded parents is unaffected, which is why no
    A-into-B test catches it. Only this one does.
    """
    a = make_node(alice, "docs")
    b = make_node(alice, "work", parent_id=a)
    c = make_node(alice, "notes.txt", parent_id=b, type_="file")
    session.commit()
    assert _count(session) == 3

    svc.move(alice, b, None)
    session.commit()

    assert _count(session) == 3, "moving to the root deleted rows"
    assert _parent_of(session, b) is None
    assert _parent_of(session, c) == b, "the subtree did not travel with its parent"
    assert _parent_of(session, a) is None


@pytest.mark.invariant
def test_orm_de_association_does_not_delete(
    session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """G-03, tested where it actually lives.

    The test above goes through the service, which writes with raw SQL -- so it
    never touches the ORM relationship and would pass even with
    `delete-orphan` restored. This one sets `parent` to None through the ORM,
    which is precisely the de-association that `delete-orphan` turns into a
    DELETE.

    Restore `cascade="all, delete-orphan"` on `Node.children` and this test
    fails. That is the only reason it exists.
    """
    a = make_node(alice, "docs")
    b = make_node(alice, "work", parent_id=a)
    c = make_node(alice, "notes.txt", parent_id=b, type_="file")
    session.commit()

    node = session.get(Node, b)
    assert node is not None
    node.parent = None
    session.flush()

    assert _count(session) == 3, "de-association through the ORM deleted rows"
    assert _parent_of(session, b) is None
    assert _parent_of(session, c) == b
    session.commit()


# ---------------------------------------------------------------- I3 cascade


@pytest.mark.invariant
def test_delete_cascades_whole_subtree(
    session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """I3. The original nulled the children's parent_id, and NULL means root."""
    a = make_node(alice, "docs")
    b = make_node(alice, "work", parent_id=a)
    make_node(alice, "deep", parent_id=b)
    make_node(alice, "notes.txt", parent_id=b, type_="file")
    session.commit()
    assert _count(session) == 4

    session.execute(text("DELETE FROM nodes WHERE id = :i"), {"i": a})
    session.commit()

    assert _count(session) == 0, "cascade left orphans behind"


# ------------------------------------------------------------ I4, I2 names


@pytest.mark.invariant
def test_duplicate_sibling_name_returns_409(
    svc: TreeService, session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """I4 and G-11. A duplicate must be a 409, not an aborted transaction.

    Without the savepoint in the service, the unique violation aborts the whole
    transaction and surfaces as a 500.
    """
    home = make_node(alice, "home")
    other = make_node(alice, "other")
    make_node(alice, "reports", parent_id=home)
    moving = make_node(alice, "reports", parent_id=other)
    session.commit()

    with pytest.raises(DuplicateName):
        svc.move(alice, moving, home)

    # The session must still be usable -- this is the half the savepoint buys.
    assert _count(session) == 4
    assert _parent_of(session, moving) == other


@pytest.mark.invariant
def test_root_names_unique_per_owner(
    session: Session, alice: int, bob: int, make_node: Callable[..., int]
) -> None:
    """I2. Scoped by owner, so the same root name is fine for two people."""
    make_node(alice, "docs")
    make_node(bob, "docs")
    session.commit()

    with pytest.raises(IntegrityError):
        make_node(alice, "docs")
        session.flush()
    session.rollback()


@pytest.mark.invariant
def test_whitespace_only_names_rejected(
    session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """G-02. `btrim(name)` with one argument trims spaces only, so a tab passed."""
    for label, value in [("space", "   "), ("tab", "\t"), ("newline", "\n"), ("crlf", "\r\n")]:
        with pytest.raises(IntegrityError, match="name_not_blank"):
            make_node(alice, value)
            session.flush()
        session.rollback()
        assert _count(session) == 0, f"a {label}-only name was stored"


# ---------------------------------------------------------------- I5 files


@pytest.mark.invariant
def test_cannot_move_into_a_file(
    svc: TreeService, session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """I5. A file is always a leaf."""
    f = make_node(alice, "notes.txt", type_="file")
    folder = make_node(alice, "docs")
    session.commit()

    with pytest.raises(FileAsParent):
        svc.move(alice, folder, f)

    assert _parent_of(session, folder) is None


# ------------------------------------------------- I6 ownership (G-01, G-04)


@pytest.mark.invariant
def test_cannot_move_another_users_node(
    svc: TreeService, session: Session, alice: int, bob: int, make_node: Callable[..., int]
) -> None:
    """G-04. The source node's owner was never checked.

    Guess an id, move any node into your own tree, delete the folder, and the
    cascade destroys the victim's subtree. 404 rather than 403 -- a 403 would
    confirm the row exists, and ids are sequential.
    """
    victim = make_node(bob, "bob-secret")
    mine = make_node(alice, "alice-home")
    session.commit()

    with pytest.raises(NodeNotFound):
        svc.move(alice, victim, mine)

    assert _parent_of(session, victim) is None


@pytest.mark.invariant
def test_cannot_move_into_another_users_folder(
    svc: TreeService, session: Session, alice: int, bob: int, make_node: Callable[..., int]
) -> None:
    """I6. The destination is checked for ownership too."""
    theirs = make_node(bob, "bob-home")
    mine = make_node(alice, "alice-docs")
    session.commit()

    with pytest.raises(NodeNotFound):
        svc.move(alice, mine, theirs)

    assert _parent_of(session, mine) is None


@pytest.mark.invariant
def test_composite_key_blocks_cross_owner_attach(
    session: Session, alice: int, bob: int, make_node: Callable[..., int]
) -> None:
    """G-01. The backstop, tested by bypassing the service entirely.

    The service checks ownership twice, so this asserts the database would
    refuse even if both checks were deleted.
    """
    theirs = make_node(bob, "bob-home")
    session.commit()

    with pytest.raises(IntegrityError, match="fk_nodes_parent_same_owner"):
        make_node(alice, "stolen", parent_id=theirs)
        session.flush()
    session.rollback()


@pytest.mark.invariant
def test_path_does_not_cross_owner(
    svc: TreeService, session: Session, alice: int, bob: int, make_node: Callable[..., int]
) -> None:
    """G-05. Breadcrumbs must not climb out of the caller's tree.

    With an owner filter only on the CTE's anchor, a node attached under a
    foreign folder would return that folder's names. The composite key makes
    the attach impossible, so this asserts the walk stays scoped anyway --
    both layers, independently.
    """
    theirs = make_node(bob, "bob-private")
    session.commit()

    with pytest.raises(NodeNotFound):
        svc.path(alice, theirs)

    mine = make_node(alice, "alice-docs")
    child = make_node(alice, "sub", parent_id=mine)
    session.commit()

    names = [row["name"] for row in svc.path(alice, child)]
    assert names == ["alice-docs", "sub"]
    assert "bob-private" not in names


@pytest.mark.invariant
def test_ancestry_walk_stops_at_the_owner_boundary(
    svc: TreeService, session: Session, alice: int, bob: int, make_node: Callable[..., int]
) -> None:
    """G-05, tested by removing the constraint that makes it unreachable.

    The composite foreign key means a cross-owner parent cannot exist, so the
    owner predicate in the recursive term of `_ANCESTRY` has no reachable data
    to defend against -- and the test above therefore could not fail if that
    predicate were deleted.

    Two independent protections, so each is tested with the other removed. Here
    the foreign key is dropped, the illegal state is built directly, and the
    walk is asserted to stop at the boundary anyway.

    Delete `AND n.owner_id = :owner_id` from `_ANCESTRY`'s recursive term and
    this test fails with bob's folder name in alice's breadcrumbs.
    """
    theirs = make_node(bob, "bob-private")
    session.commit()

    session.execute(text("ALTER TABLE nodes DROP CONSTRAINT fk_nodes_parent_same_owner"))
    try:
        smuggled = make_node(alice, "mine", parent_id=theirs)
        session.commit()

        names = [row["name"] for row in svc.path(alice, smuggled)]
        assert names == ["mine"], f"the walk crossed an owner boundary: {names}"
        assert "bob-private" not in names
    finally:
        session.execute(text("DELETE FROM nodes WHERE owner_id = :o"), {"o": alice})
        session.commit()
        session.execute(
            text(
                "ALTER TABLE nodes ADD CONSTRAINT fk_nodes_parent_same_owner "
                "FOREIGN KEY (parent_id, owner_id) REFERENCES nodes (id, owner_id) "
                "ON DELETE CASCADE"
            )
        )
        session.commit()


# ---------------------------------------------------------------- I7 depth


@pytest.mark.invariant
def test_depth_counts_subtree_height(
    svc: TreeService, session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """G-09. The bound includes the height of the subtree being carried.

    Measuring only the destination's level lets two legal deep chains compose
    into one that is twice as deep, in a single move.
    """
    # A chain occupying most of the budget.
    deep: int | None = None
    for i in range(MAX_DEPTH - 3):
        deep = make_node(alice, f"d{i}", parent_id=deep)

    # A short chain that will not fit underneath it.
    tall_root = make_node(alice, "tall")
    tall: int | None = tall_root
    for i in range(5):
        tall = make_node(alice, f"t{i}", parent_id=tall)
    session.commit()

    with pytest.raises(DepthExceeded):
        svc.move(alice, tall_root, deep)

    assert _parent_of(session, tall_root) is None


@pytest.mark.invariant
def test_move_within_depth_budget_allowed(
    svc: TreeService, session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """I7. The cap must not reject legal moves."""
    a = make_node(alice, "a")
    b = make_node(alice, "b")
    session.commit()

    svc.move(alice, b, a)
    session.commit()

    assert _parent_of(session, b) == a


# ---------------------------------------------------------------- G-10


@pytest.mark.invariant
def test_move_nonexistent_node_returns_404(
    svc: TreeService, session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """G-10. The original reported this as 200 OK."""
    home = make_node(alice, "home")
    session.commit()

    with pytest.raises(NodeNotFound):
        svc.move(alice, 999_999, home)


@pytest.mark.invariant
def test_move_to_nonexistent_destination_returns_404(
    svc: TreeService, session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """G-10, mirrored. The original raised a foreign key error and a 500."""
    a = make_node(alice, "docs")
    session.commit()

    with pytest.raises(NodeNotFound):
        svc.move(alice, a, 999_999)

    assert _parent_of(session, a) is None


@pytest.mark.invariant
def test_move_to_current_parent_is_a_noop(
    svc: TreeService, session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    a = make_node(alice, "docs")
    b = make_node(alice, "work", parent_id=a)
    session.commit()

    svc.move(alice, b, a)
    session.commit()

    assert _parent_of(session, b) == a
    assert _count(session) == 2


@pytest.mark.invariant
def test_probe_orm_de_association_paths(
    session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """Temporary probe. Which ORM path, if any, triggers delete-orphan here?

    Deleted once the question is answered. Reports rather than asserts, so the
    output tells us which paths are dangerous instead of only that one is.
    """
    results = {}

    # path A: set child.parent = None, parent's collection never loaded
    a = make_node(alice, "a1")
    b = make_node(alice, "b1", parent_id=a)
    session.commit()
    n = session.get(Node, b)
    assert n is not None
    n.parent = None
    session.flush()
    results["set_parent_none_cold"] = _count(session)
    session.rollback()

    # path B: load the parent's children collection first, then detach
    n2 = session.get(Node, b)
    p2 = session.get(Node, a)
    assert n2 is not None and p2 is not None
    _ = list(p2.children)
    n2.parent = None
    session.flush()
    results["set_parent_none_warm"] = _count(session)
    session.rollback()

    # path C: remove from the collection directly - the canonical trigger
    p3 = session.get(Node, a)
    assert p3 is not None
    kids = list(p3.children)
    if kids:
        p3.children.remove(kids[0])
        session.flush()
    results["collection_remove"] = _count(session)
    session.rollback()

    raise AssertionError(f"PROBE rows remaining (2 = kept, 1 = DELETED): {results}")
