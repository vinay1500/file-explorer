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
    """It travels in a query string, so `+`, `/` and `=` would all need escaping.

    The input is chosen so standard base64 *would* produce both `+` and `/`.
    The first version used a different string and happened to encode to neither,
    so swapping `urlsafe_b64encode` for `b64encode` survived it — a test that
    could not tell the two apart while being named for the difference.
    """
    name = "".join(chr(c) for c in (0x3EB, 0x3FF, 0x7F8, 0x7FE)) * 8
    standard = base64.b64encode(f"{name}\x1f9".encode()).decode()
    assert "+" in standard and "/" in standard, (
        "the input no longer distinguishes the two alphabets, so this test is blind"
    )

    token = cursors.encode((name, 9))
    assert not set(token) & set("+/="), f"not URL-safe: {token}"
    assert cursors.decode(token) == (name, 9)


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
def test_a_cursor_carries_no_identity() -> None:
    """Why the cursor is unsigned, stated as something that can be wrong.

    The argument is that signing buys nothing because a cursor is only ever a
    position inside an already owner-scoped query. That argument depends on one
    checkable fact: the cursor carries no owner.

    The first version of this asserted `str(bob) not in encode(...)` and ended
    with `or alice == 1`, which is true whenever alice is id 1 — always, under
    `RESTART IDENTITY`. It was a tautology wrapped around an assertion that
    depended on an id not colliding by chance.
    """
    import inspect

    source = inspect.getsource(cursors.encode)
    assert "owner" not in source, (
        "encode() mentions an owner, so a cursor may carry identity and the "
        "unsigned argument no longer holds"
    )

    # And the round trip preserves exactly two fields, neither of them an owner.
    decoded = cursors.decode(cursors.encode(("name", 7)))
    assert decoded == ("name", 7)
    assert len(decoded) == 2  # type: ignore[arg-type]
