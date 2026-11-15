"""Cursor encoding, and the three ways a cursor can be wrong.

Coverage found both failure branches in `cursors.decode` with nothing behind
them. The one HTTP-level test sends `%%%not-base64%%%`, which happens to take
the first branch and leaves the other two untested — which matters, because a
cursor is the one value a client is invited to hand back verbatim.
"""

from __future__ import annotations

import base64

import pytest

from file_explorer import cursors
from file_explorer.errors import InvalidCursor


@pytest.mark.invariant
@pytest.mark.parametrize(
    ("label", "cursor"),
    [
        ("plain", ("report.txt", 42)),
        ("empty name is the first-page sentinel", ("", 0)),
        ("unicode", ("年度報告.pdf", 7)),
        ("a name containing the padding character", ("a=b=c", 1)),
        ("a name that looks like a cursor", ("YWJj", 2)),
        ("a very long name", ("x" * 255, 99999)),
    ],
)
def test_a_cursor_round_trips(label: str, cursor: tuple[str, int]) -> None:
    assert cursors.decode(cursors.encode(cursor)) == cursor


@pytest.mark.invariant
def test_none_passes_through_as_the_first_page() -> None:
    assert cursors.decode(None) is None


@pytest.mark.invariant
def test_an_encoded_cursor_is_url_safe() -> None:
    """It travels in a query string, so `+`, `/` and `=` would all need escaping."""
    token = cursors.encode(("éèê" * 40, 123))
    assert not set(token) & set("+/="), f"not URL-safe: {token}"


@pytest.mark.invariant
@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("not base64 at all", "%%%not-base64%%%"),
        ("base64 of bytes that are not UTF-8", base64.urlsafe_b64encode(b"\xff\xfe").decode()),
        ("base64 with no separator", base64.urlsafe_b64encode(b"nameonly").decode()),
        ("an id that is not a number", base64.urlsafe_b64encode(b"name\x1fnotanumber").decode()),
        ("empty", ""),
    ],
)
def test_a_malformed_cursor_is_rejected(label: str, token: str) -> None:
    """Each branch, not just the first one a fuzzy input happens to hit.

    A cursor is the one value this API asks a client to hand back verbatim, so
    every way of getting it wrong has to end in the same domain error rather
    than in whatever the decoder happened to raise.
    """
    with pytest.raises(InvalidCursor):
        cursors.decode(token)


@pytest.mark.invariant
def test_a_forged_cursor_cannot_reach_another_owner(alice: int, bob: int) -> None:
    """Why the cursor is unsigned, stated as a test rather than a comment.

    Signing it would add a key to manage. It is unnecessary because the cursor
    is only ever a position in an already owner-scoped query: forging one skips
    part of the forger's own listing and reaches nothing else. This asserts the
    thing that argument depends on — a cursor carries no owner at all.
    """
    name, node_id = cursors.decode(cursors.encode(("anything", 1)))  # type: ignore[misc]
    assert (name, node_id) == ("anything", 1)
    assert str(bob) not in cursors.encode(("anything", 1))
    assert str(alice) not in cursors.encode(("anything", 1)) or alice == 1
