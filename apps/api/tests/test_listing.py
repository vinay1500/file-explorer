"""`list_children`, `list_roots`, and the collation underneath them.

Keyset pagination asks "give me the rows after this one" in sort order. That is
one index seek whatever the page number, and it is stable while other people are
writing — but it only works while the stored order and the comparison agree.

Which makes the collation load-bearing. A text index built under one collation
and compared under another disagrees with itself, and the failure is silent:
pages skip rows while every individual page still looks plausible.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from file_explorer.errors import NodeNotFound
from file_explorer.services.tree import MAX_PAGE, TreeService

MAX_DEPTH = 64


@pytest.fixture
def svc(session: Session) -> TreeService:
    return TreeService(session, MAX_DEPTH)


def _names(page: tuple[list[dict[str, object]], object]) -> list[str]:
    return [str(row["name"]) for row in page[0]]


def _drain(fetch: Callable[[object], tuple[list[dict[str, object]], object]]) -> list[str]:
    """Page all the way through, collecting names in order."""
    seen: list[str] = []
    cursor: object = None
    for _ in range(100):  # a bound, so a cursor bug is a failure and not a hang
        items, cursor = fetch(cursor)
        seen.extend(str(row["name"]) for row in items)
        if cursor is None:
            return seen
    raise AssertionError("pagination did not terminate within 100 pages")


# ------------------------------------------------------------- the basics


@pytest.mark.invariant
def test_children_come_back_in_name_order(svc: TreeService, alice: int) -> None:
    home = svc.create(alice, None, "home", "folder")
    for name in ("delta", "alpha", "charlie", "bravo"):
        svc.create(alice, home, name, "file")

    assert _names(svc.list_children(alice, home)) == ["alpha", "bravo", "charlie", "delta"]


@pytest.mark.invariant
def test_roots_come_back_in_name_order(svc: TreeService, alice: int) -> None:
    for name in ("work", "archive", "photos"):
        svc.create(alice, None, name, "folder")

    assert _names(svc.list_roots(alice)) == ["archive", "photos", "work"]


@pytest.mark.invariant
def test_a_listing_stops_at_the_owner_boundary(
    svc: TreeService, session: Session, alice: int, bob: int, make_node: Callable[..., int]
) -> None:
    make_node(bob, "theirs")
    svc.create(alice, None, "mine", "folder")
    session.commit()

    assert _names(svc.list_roots(alice)) == ["mine"]
    assert _names(svc.list_roots(bob)) == ["theirs"]


@pytest.mark.invariant
def test_children_of_a_foreign_folder_is_404_not_an_empty_page(
    svc: TreeService, session: Session, alice: int, bob: int, make_node: Callable[..., int]
) -> None:
    """An empty page would make "nothing here" and "not yours" the same answer."""
    theirs = make_node(bob, "bob-private")
    make_node(bob, "bob-child", parent_id=theirs)
    session.commit()

    with pytest.raises(NodeNotFound):
        svc.list_children(alice, theirs)


@pytest.mark.invariant
def test_children_of_a_missing_folder_is_404(svc: TreeService, alice: int) -> None:
    with pytest.raises(NodeNotFound):
        svc.list_children(alice, 999999)


@pytest.mark.invariant
def test_an_empty_folder_is_an_empty_page_with_no_cursor(svc: TreeService, alice: int) -> None:
    home = svc.create(alice, None, "home", "folder")
    items, cursor = svc.list_children(alice, home)
    assert items == []
    assert cursor is None


# ----------------------------------------------------------- the paging


@pytest.mark.invariant
def test_paging_returns_every_row_exactly_once(svc: TreeService, alice: int) -> None:
    """The property that matters. Not "pages look right" — nothing lost, nothing twice."""
    home = svc.create(alice, None, "home", "folder")
    expected = [f"file-{n:03d}" for n in range(37)]
    for name in expected:
        svc.create(alice, home, name, "file")

    def page(cursor: object) -> tuple[list[dict[str, object]], object]:
        return svc.list_children(alice, home, limit=5, after=cursor)  # type: ignore[arg-type]

    seen = _drain(page)

    assert seen == expected, "paging lost, duplicated or reordered rows"
    assert len(seen) == len(set(seen))


@pytest.mark.invariant
def test_the_last_page_carries_no_cursor(svc: TreeService, alice: int) -> None:
    """A full final page must not hand back a cursor that leads nowhere.

    This is why one row beyond the page is fetched: a page being full is not
    evidence that another page exists. Ten rows at a page size of five would
    otherwise end with a cursor and a trailing empty page.
    """
    home = svc.create(alice, None, "home", "folder")
    for n in range(10):
        svc.create(alice, home, f"f{n}", "file")

    _, first = svc.list_children(alice, home, limit=5)
    assert first is not None, "a second page exists, so the first must offer a cursor"

    items, second = svc.list_children(alice, home, limit=5, after=first)
    assert len(items) == 5
    assert second is None, "the final page handed back a cursor to an empty page"


@pytest.mark.invariant
def test_a_row_inserted_behind_the_cursor_does_not_shift_the_page(
    svc: TreeService, alice: int
) -> None:
    """What keyset buys over OFFSET.

    With OFFSET, inserting a row before the window shifts everything along by
    one and the next page silently skips a row. The cursor is the sort key, so
    it is unaffected.
    """
    home = svc.create(alice, None, "home", "folder")
    for name in ("b", "d", "f", "h"):
        svc.create(alice, home, name, "file")

    first, cursor = svc.list_children(alice, home, limit=2)
    assert [str(r["name"]) for r in first] == ["b", "d"]

    svc.create(alice, home, "a", "file")  # lands behind the cursor

    rest, _ = svc.list_children(alice, home, limit=2, after=cursor)
    assert [str(r["name"]) for r in rest] == ["f", "h"], (
        "an insert behind the cursor shifted the window, which is the OFFSET bug"
    )


@pytest.mark.invariant
def test_the_page_size_is_clamped(svc: TreeService, alice: int) -> None:
    """A caller-supplied limit is an input, not an instruction.

    Unbounded it is a denial-of-service knob: one request asks for every row the
    owner has and holds a connection while PostgreSQL materialises it.
    """
    home = svc.create(alice, None, "home", "folder")
    # More rows than the cap, or asking for a billion proves only that nothing
    # crashed. The cap has to be the thing that stops it.
    for n in range(MAX_PAGE + 5):
        svc.create(alice, home, f"f{n:04d}", "file")

    assert len(svc.list_children(alice, home, limit=10**9)[0]) == MAX_PAGE
    assert len(svc.list_children(alice, home, limit=0)[0]) == 1, "a limit of 0 must not mean all"
    assert len(svc.list_children(alice, home, limit=-5)[0]) == 1


@pytest.mark.invariant
def test_the_blank_name_constraint_still_exists(session: Session) -> None:
    """The schema precondition the `('', 0)` sentinel rests on.

    Renamed: this asserts a row in `pg_constraint`, not anything in `tree.py`.
    Change `_FIRST_PAGE` to `("\xff", 0)` and it still passes. The paging
    behaviour itself is covered by `test_paging_returns_every_row_exactly_once`;
    what this guards is the assumption underneath it.

    The sentinel is sound because no stored name can be empty.

    One statement serves the first page and every page after it only while that
    holds. If `ck_nodes_name_not_blank` were ever dropped, a row with an empty
    name would sort at or below the sentinel and the first page would skip it.
    """
    present = session.execute(
        text(
            "SELECT 1 FROM pg_constraint WHERE conrelid = 'nodes'::regclass "
            "AND conname = 'ck_nodes_name_not_blank'"
        )
    ).one_or_none()
    assert present is not None, (
        "ck_nodes_name_not_blank is gone, so ('', 0) is no longer guaranteed to "
        "sort below every stored name and the first page can skip a row"
    )


# -------------------------------------------------------- the collation


@pytest.mark.invariant
def test_the_name_indexes_pin_their_collation(session: Session) -> None:
    """The guard for migration 0002, asserted against the catalogue.

    Deliberately not a behavioural ordering test. This suite's PostgreSQL is
    initialised with `--locale=C`, so the database default already *is* byte
    order and an ordering test would pass whether or not the collation was ever
    pinned. Production is not initialised that way.

    The catalogue does not care what the default is: it records the collation
    the index was actually built with.
    """
    rows = session.execute(
        text("""
        SELECT i.relname, co.collname
          FROM pg_index x
          JOIN pg_class i ON i.oid = x.indexrelid
          JOIN pg_class t ON t.oid = x.indrelid
          JOIN pg_collation co ON co.oid = ANY (x.indcollation)
         WHERE t.relname = 'nodes'
        """)
    ).all()
    pinned = {r[0]: r[1] for r in rows}

    for index in ("uq_nodes_parent_name", "uq_nodes_root_name"):
        assert index in pinned, f"{index} indexes no collatable column — has it changed?"
        assert pinned[index] == "C", (
            f"{index} is built under collation {pinned[index]!r}, not 'C'. "
            "A glibc or ICU upgrade can then reorder it underneath the keyset "
            "comparison, and pages start skipping rows silently."
        )


@pytest.mark.invariant
def test_ordering_is_byte_order_and_that_is_a_real_choice(
    svc: TreeService, session: Session, alice: int
) -> None:
    """Byte order, demonstrated against a linguistic collation that disagrees.

    `Zebra` before `apple` is the visible cost of `COLLATE "C"`, and it is a
    deliberate trade: the client can display rows in any order it likes, but
    pagination and uniqueness need the stored order to be *stable*, which no
    locale-dependent collation is.

    The ICU collation here exists only to prove the two orders genuinely differ
    on this data — without it, a byte-order assertion on a `--locale=C` database
    proves nothing.
    """
    try:
        session.execute(
            text("CREATE COLLATION IF NOT EXISTS linguistic (provider = icu, locale = 'en-US')")
        )
        session.commit()
    except Exception:
        session.rollback()
        pytest.skip("no ICU provider here; test_the_name_indexes_pin_their_collation still guards")

    home = svc.create(alice, None, "home", "folder")
    for name in ("Zebra", "apple", "Banana"):
        svc.create(alice, home, name, "file")
    session.commit()

    linguistic = [
        r[0]
        for r in session.execute(
            text(
                "SELECT name FROM nodes WHERE parent_id = :p ORDER BY name COLLATE linguistic, id"
            ),
            {"p": home},
        ).all()
    ]
    byte_order = _names(svc.list_children(alice, home))

    assert byte_order == ["Banana", "Zebra", "apple"], f"not byte order: {byte_order}"
    assert linguistic != byte_order, (
        "the ICU collation agrees with byte order on this data, so this test "
        f"cannot tell them apart: {linguistic}"
    )
