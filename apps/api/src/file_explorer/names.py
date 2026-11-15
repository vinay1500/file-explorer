"""Validating and normalising a node name.

The database check only guarantees a name is not blank after ASCII whitespace
is trimmed. That leaves a lot: a name made of one non-breaking space, a name
carrying a NUL the driver cannot even send, two names that look identical on
screen and are different byte strings, and a name that renders as something
other than what it is.

The last one is the interesting case for a file manager, because the whole UI
is a list of names a person reads and clicks.
"""

from __future__ import annotations

import unicodedata

from file_explorer.errors import InvalidName
from file_explorer.limits import MAX_NAME_LENGTH

# Directional overrides and isolates -- the Trojan Source set. These reorder
# how the rest of the string renders, so `report` + an override can display as
# `report.txt` while being something else entirely. There is no legitimate use
# for one inside a filename.
_BIDI_CONTROL = frozenset(range(0x202A, 0x202F)) | frozenset(range(0x2066, 0x206A))

# Invisible characters with no text-shaping role: zero-width space, word
# joiner, and the byte-order mark. Two names differing only by one of these
# look identical in every font, so a person cannot tell which they are clicking.
_INVISIBLE = frozenset({0x200B, 0x2060, 0xFEFF})

# C0 controls and DEL, then C1. NUL is the sharp one -- PostgreSQL text cannot
# hold it at all, so without this check it surfaces as a driver ValueError
# rather than a 422. The rest break terminals, logs and filenames.
_CONTROL = frozenset(range(0x00, 0x20)) | {0x7F} | frozenset(range(0x80, 0xA0))

FORBIDDEN = _CONTROL | _BIDI_CONTROL | _INVISIBLE

# NOT forbidden, deliberately:
#
#   U+200C ZERO WIDTH NON-JOINER and U+200D ZERO WIDTH JOINER carry meaning in
#   Persian, Arabic and several Indic scripts -- they are the difference
#   between two spellings, not decoration. Banning them would make a class of
#   perfectly ordinary names unrepresentable.
#
#   U+200E LEFT-TO-RIGHT MARK and U+200F RIGHT-TO-LEFT MARK are marks, not
#   overrides: they resolve direction for a neighbouring character rather than
#   reordering the string. Mixed-direction names need them.
#
# Both are judgement calls, and both widen the attack surface slightly. The
# trade accepted here is that a name must be *representable* in the user's own
# language; the spoofing that actually matters -- a name that renders as a
# different name -- needs the override set above, which is blocked.


def clean(raw: object) -> str:
    """Normalise a submitted name, or raise `InvalidName`.

    Returns what should be stored. Callers must store this rather than what
    they were given, because the uniqueness indexes compare stored bytes -- so
    normalising at the edge is what stops `café` and `cafe` + a combining
    accent becoming two siblings that cannot be told apart.
    """
    if not isinstance(raw, str):
        raise InvalidName

    # NFC first. Composing before measuring means the length limit counts what
    # will actually be stored, and the duplicate check compares like with like.
    name = unicodedata.normalize("NFC", raw)

    # Strip Unicode whitespace, not just ASCII. `str.strip()` with no argument
    # removes NBSP and friends; the database check only knows about the six
    # ASCII ones, so a name of a single NBSP is storable without this.
    name = name.strip()

    if not name:
        raise InvalidName

    for char in name:
        if ord(char) in FORBIDDEN:
            raise InvalidName

    if len(name) > MAX_NAME_LENGTH:
        raise InvalidName

    return name
