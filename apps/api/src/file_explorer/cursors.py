"""Encoding the keyset cursor for the wire.

The service's cursor is the sort key itself, `(name, id)`. That is the right
shape internally and the wrong thing to put in a URL unencoded: it contains a
user-chosen name, so it needs escaping, and exposing it as two query parameters
invites a client to construct one by hand and then depend on the sort order
never changing.

Base64url of `name` and `id` joined by a unit separator. Opaque, URL-safe, and
cheap to widen later -- a cursor is a token the server issued, not a contract.

It is **not** signed, and does not need to be. Every query it feeds is scoped
by `owner_id` from `require_actor()`, so the worst a forged cursor does is skip
part of the forger's own listing.
"""

from __future__ import annotations

import base64
import binascii

from file_explorer.errors import InvalidCursor

_SEP = "\x1f"  # UNIT SEPARATOR: cannot appear in a stored name (category Cc)


def encode(cursor: tuple[str, int]) -> str:
    name, node_id = cursor
    raw = f"{name}{_SEP}{node_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(token: str | None) -> tuple[str, int] | None:
    """Decode a cursor, or raise `InvalidCursor`. None passes through."""
    if token is None:
        return None
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        name, _, node_id = raw.rpartition(_SEP)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCursor from exc
    if not _:
        raise InvalidCursor
    try:
        return name, int(node_id)
    except ValueError as exc:
        raise InvalidCursor from exc
