"""Validating and normalising a node name.

The database check only guarantees a name is not blank after ASCII whitespace
is trimmed. That leaves a lot: a name made of one non-breaking space, a name
carrying a NUL the driver cannot even send, two names that look identical on
screen and are different byte strings, and a name that renders as something
other than what it is.

The last one matters most for a file manager, because the whole interface is a
list of names a person reads and clicks.

**What this does not cover.** Homoglyphs across scripts are not addressed:
Latin `o` (U+006F), Greek omicron (U+03BF) and Cyrillic small o (U+043E) all
render identically. Nor are visually similar spaces inside a name. Catching
those needs a confusable-skeleton check against the Unicode security data,
which is a different piece of work with real false-positive risk for
multilingual names.

What is covered is narrower, and stated as such: **look-alikes built from
characters that render as nothing, and names that reorder their own display.**
"""

from __future__ import annotations

import unicodedata

from file_explorer.errors import InvalidName
from file_explorer.limits import MAX_NAME_LENGTH

# Rejected by Unicode general category rather than by an enumerated list.
#
# The list came first and the list was wrong: it named three invisible
# characters with no stated selection rule, so SOFT HYPHEN, MONGOLIAN VOWEL
# SEPARATOR and the rest went straight through, and the next one to come up
# would have been another patch. A category covers the class.
#
#   Cc  C0 and C1 controls, and DEL. NUL is the sharp one -- PostgreSQL text
#       cannot hold it, so without this it surfaces as a driver ValueError
#       rather than a 422. The rest break terminals, logs and filenames.
#   Cf  Format characters. This is the whole invisible class: the directional
#       overrides and isolates (the Trojan Source set), ZERO WIDTH SPACE, WORD
#       JOINER, the byte-order mark, SOFT HYPHEN, and anything added later.
#   Cs  Surrogates. A lone surrogate cannot be encoded to UTF-8 at all and
#       fails inside the driver, which is the NUL problem again.
#   Zl  LINE SEPARATOR, Zp PARAGRAPH SEPARATOR. `str.strip()` removes these at
#       the edges and not in the middle, where they do exactly what the control
#       characters do.
REJECTED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})

# Allowed back in, each for a reason.
#
#   U+200C ZWNJ and U+200D ZWJ are the difference between two spellings in
#   Persian, Arabic and several Indic scripts. They are orthography, not
#   decoration.
#
#   U+200E LRM, U+200F RLM and U+061C ALM are *marks*: they give a neighbouring
#   character a direction rather than reordering the string, which is what the
#   override set does. Mixed-direction names need them, and ALM is the Arabic
#   counterpart of the other two -- leaving it out while allowing them would be
#   an arbitrary line drawn at the edge of one alphabet.
#
# Each of these widens the attack surface slightly. The trade is deliberate: a
# name has to be representable in the writer's own language, and a deny-list
# that makes ordinary Persian or Arabic names impossible is not a safer system,
# only a narrower one.
ALLOWED_FORMAT = frozenset(
    {
        "\u200c",  # ZERO WIDTH NON-JOINER
        "\u200d",  # ZERO WIDTH JOINER
        "\u200e",  # LEFT-TO-RIGHT MARK
        "\u200f",  # RIGHT-TO-LEFT MARK
        "\u061c",  # ARABIC LETTER MARK
    }
)

# Characters that render as nothing but are *letters* by category, so no rule
# above reaches them. U+3164 HANGUL FILLER is the one people actually use for
# this; the others are its relatives. U+034F COMBINING GRAPHEME JOINER is a
# mark, invisible, and has no business in a filename.
REJECTED_EXPLICITLY = frozenset(
    {
        "\u115f",  # HANGUL CHOSEONG FILLER
        "\u1160",  # HANGUL JUNGSEONG FILLER
        "\u3164",  # HANGUL FILLER
        "\uffa0",  # HALFWIDTH HANGUL FILLER
        "\u034f",  # COMBINING GRAPHEME JOINER
    }
)


def is_rejected(char: str) -> bool:
    """Whether a single character may not appear in a name."""
    if char in ALLOWED_FORMAT:
        return False
    if char in REJECTED_EXPLICITLY:
        return True
    return unicodedata.category(char) in REJECTED_CATEGORIES


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

    if any(is_rejected(char) for char in name):
        raise InvalidName

    if len(name) > MAX_NAME_LENGTH:
        raise InvalidName

    return name


# --- email ----------------------------------------------------------------

# The practical maximum per RFC 3696 erratum 1690, matching `models/user.py`.
MAX_EMAIL_LENGTH = 320


def normalise_email(raw: str) -> str:
    """One spelling per address, decided in Python rather than by the database.

    `uq_users_email_lower` is a unique index on `lower(email)`, and `lower()`
    does not mean the same thing everywhere this runs. The deployed database is
    `C.UTF-8` under PostgreSQL's **builtin** provider, where `lower()` applies
    Unicode simple case mapping; CI's containers are `--locale=C` under
    **libc**, where only ASCII `A`-`Z` are letters. So the index disagrees with
    itself across environments about whether two addresses are one account --
    in both directions, and the CI assertion that looks like it covers this
    uses `A@EXAMPLE.COM`, which is pure ASCII and passes either way.

    Normalising before the address reaches the index makes that divergence stop
    mattering: every row is stored in one spelling, so `lower()` has nothing
    left to disagree about. The index stays as a second line of defence.

    What this does, and the reason for each:

    * **NFC**, like node names, so two byte sequences that render identically
      cannot become two accounts.
    * **Strip surrounding whitespace.** A pasted address regularly carries it,
      and an address stored with a trailing space is one no later lookup will
      match -- the user would be told their password is wrong forever.
    * **Casefold**, not `lower()`. Full Unicode case folding, done once, in a
      place whose behaviour does not depend on how the database was
      initialised.

    What it deliberately does not do: strip dots, drop `+tags`, or treat
    provider-specific aliasing as equivalence. Those are rules about particular
    mail providers, they change, and getting them wrong merges two people's
    accounts.
    """
    return unicodedata.normalize("NFC", raw).strip().casefold()
