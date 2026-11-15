"""The error paths that were called unreachable and are not.

The coverage floor was set at 98 with a comment claiming fourteen lines were
"genuinely out of reach from a test". Review checked each one: the claim held
for exactly one of them, and three were contradicted by techniques used
elsewhere in the same change — a test that patches `delete_node` to return 0
sat a few hundred lines from an identical construct declared unreachable.

So this file reaches them. Every one is a real failure path: the row went away
between the read and the write, the lock could not be taken, a constraint the
service believes it already prevented fired anyway. They are the paths that run
on the worst day, and "hard to reach from a test" was never the same claim as
"cannot be reached".
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from file_explorer.app import _retype_default_error
from file_explorer.errors import FileAsParent, NodeNotFound
from file_explorer.services.tree import TreeService

MAX_DEPTH = 64


@pytest.fixture
def svc(session: Session) -> TreeService:
    return TreeService(session, MAX_DEPTH)


# ------------------------------------------------ the row went away


@pytest.mark.invariant
def test_getting_a_node_that_is_not_there_is_404(svc: TreeService, alice: int) -> None:
    """An ordinary not-found, which the floor's comment called a race."""
    with pytest.raises(NodeNotFound):
        svc.get(alice, 999999)


@pytest.mark.invariant
def test_a_move_whose_node_vanishes_before_the_lock_is_404(
    svc: TreeService, alice: int, make_node: Callable[..., int], session: Session
) -> None:
    """The lock returned without the node in it, so it went between read and lock."""
    home = make_node(alice, "home")
    mover = make_node(alice, "mover")
    session.commit()

    original = svc._repo.lock_move_participants

    def without_the_mover(owner_id: int, node_id: int, new_parent_id: int | None) -> list[int]:
        return [i for i in original(owner_id, node_id, new_parent_id) if i != mover]

    svc._repo.lock_move_participants = without_the_mover  # type: ignore[method-assign]

    with pytest.raises(NodeNotFound):
        svc.move(alice, mover, home)
    session.rollback()


@pytest.mark.invariant
def test_a_move_whose_destination_vanishes_while_waiting_is_404(
    svc: TreeService, alice: int, make_node: Callable[..., int], session: Session
) -> None:
    """Step 3 re-reads the destination after the lock. This is why."""
    home = make_node(alice, "home")
    mover = make_node(alice, "mover")
    session.commit()

    original = svc._repo.get
    seen: list[int] = []

    def gone_on_the_recheck(owner_id: int, node_id: int) -> object:
        seen.append(node_id)
        if node_id == home and seen.count(home) > 1:
            return None  # deleted while we waited for the lock
        return original(owner_id, node_id)

    svc._repo.get = gone_on_the_recheck  # type: ignore[method-assign]

    with pytest.raises(NodeNotFound):
        svc.move(alice, mover, home)
    session.rollback()


@pytest.mark.invariant
def test_a_destination_that_becomes_a_file_while_waiting_is_409(
    svc: TreeService, alice: int, make_node: Callable[..., int], session: Session
) -> None:
    """I5 on the re-check, not the first read. Not a deletion — the floor's
    comment filed it under one."""
    home = make_node(alice, "home")
    mover = make_node(alice, "mover")
    session.commit()

    original = svc._repo.get
    seen: list[int] = []

    def a_file_on_the_recheck(owner_id: int, node_id: int) -> object:
        seen.append(node_id)
        row = original(owner_id, node_id)
        if node_id == home and seen.count(home) > 1 and row is not None:
            return SimpleNamespace(id=row.id, parent_id=row.parent_id, name=row.name, type="file")
        return row

    svc._repo.get = a_file_on_the_recheck  # type: ignore[method-assign]

    with pytest.raises(FileAsParent):
        svc.move(alice, mover, home)
    session.rollback()


@pytest.mark.invariant
def test_a_move_that_updates_nothing_is_404(
    svc: TreeService, alice: int, make_node: Callable[..., int], session: Session
) -> None:
    """The rowcount check. `move(999999, 7)` used to answer 200."""
    home = make_node(alice, "home")
    mover = make_node(alice, "mover")
    session.commit()

    svc._repo.set_parent = lambda owner_id, node_id, new_parent_id: 0  # type: ignore[method-assign]

    with pytest.raises(NodeNotFound):
        svc.move(alice, mover, home)
    session.rollback()


@pytest.mark.invariant
def test_a_rename_that_updates_nothing_is_404(
    svc: TreeService, alice: int, make_node: Callable[..., int], session: Session
) -> None:
    """Same construct as `move`'s, and it was declared unreachable while the
    delete version of it has a test."""
    node_id = make_node(alice, "old")
    session.commit()

    svc._repo.set_name = lambda owner_id, node_id, name: 0  # type: ignore[method-assign]

    with pytest.raises(NodeNotFound):
        svc.rename(alice, node_id, "new")
    session.rollback()


@pytest.mark.invariant
def test_a_create_whose_parent_vanishes_before_the_lock_is_404(
    svc: TreeService, alice: int, make_node: Callable[..., int], session: Session
) -> None:
    """An empty lock set means the parent went between the read and the lock.

    The test for the chain-coverage check deliberately avoids returning `[]`
    "because it would trip this check first", which is as close to admitting a
    line is reachable as a comment gets.
    """
    home = make_node(alice, "home")
    session.commit()

    svc._repo.lock_ancestry = lambda owner_id, node_id: []  # type: ignore[method-assign]

    with pytest.raises(NodeNotFound):
        svc.create(alice, home, "child", "folder")
    session.rollback()


@pytest.mark.invariant
def test_a_delete_whose_node_cannot_be_locked_is_404(
    svc: TreeService, alice: int, make_node: Callable[..., int], session: Session
) -> None:
    doomed = make_node(alice, "doomed")
    session.commit()

    svc._repo.lock_one = lambda owner_id, node_id: False  # type: ignore[method-assign]

    with pytest.raises(NodeNotFound):
        svc.delete(alice, doomed)
    session.rollback()


# ------------------------------------------- the database caught us out


@pytest.mark.invariant
def test_the_composite_key_firing_is_a_404_and_is_logged(
    svc: TreeService, caplog: pytest.LogCaptureFixture
) -> None:
    """The backstop for I6, which only fires when the service has already failed.

    Its own comment says "Should be unreachable: ownership is checked twice
    above. If it fires, the database caught something the service missed" —
    which is exactly why the translation needs a test. A branch that runs only
    after something else has gone wrong is the worst one to discover is wrong.
    """
    import logging

    from file_explorer.errors import TreeError
    from tests.test_error_mapping import _integrity_error

    with caplog.at_level(logging.ERROR):
        result = svc._translate(_integrity_error("fk_nodes_parent_same_owner"))

    assert isinstance(result, NodeNotFound)
    assert isinstance(result, TreeError)
    assert "[SQL:" not in str(result)
    assert any("composite owner key" in r.getMessage() for r in caplog.records), (
        "the database caught what the service missed and nothing recorded it"
    )


# ------------------------------------------------ the spec diagnostics


@pytest.mark.invariant
def test_the_retype_says_so_when_it_cannot_find_the_components(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both warning arms, which the "or says why" test never reached.

    `_retype_default_error` takes the api object as an argument, so a stub
    covers both. Calling it unreachable was never true; it was untried.
    """
    import logging

    no_responses = SimpleNamespace(
        spec=SimpleNamespace(components=SimpleNamespace(responses=None)),
        DEFAULT_ERROR_RESPONSE_NAME="DEFAULT_ERROR",
    )
    with caplog.at_level(logging.WARNING):
        _retype_default_error(no_responses)  # type: ignore[arg-type]
    assert any("components.responses is" in r.getMessage() for r in caplog.records)

    caplog.clear()
    nothing_to_retype = SimpleNamespace(
        spec=SimpleNamespace(components=SimpleNamespace(responses={"Other": {"content": {}}})),
        DEFAULT_ERROR_RESPONSE_NAME="DEFAULT_ERROR",
    )
    with caplog.at_level(logging.WARNING):
        _retype_default_error(nothing_to_retype)  # type: ignore[arg-type]
    assert any("no error response component was retyped" in r.getMessage() for r in caplog.records)
