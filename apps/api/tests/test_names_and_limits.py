"""Name validation, the per-owner ceiling, and the configuration wiring.

Gate 1 recorded two things here. Four configuration values were read by
nothing, so the service ran on whatever its callers hardcoded. And the only
name rule was a database check that trims six ASCII whitespace characters —
which leaves a name made of one non-breaking space, a name carrying a NUL the
driver cannot send, and a name that renders as a different name.

The last is the one that matters for a file manager, because the entire
interface is a list of names a person reads and clicks.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable

import pytest
from flask import Flask
from sqlalchemy.orm import Session

from file_explorer.app import create_app, tree_service
from file_explorer.config import Config, engine_options
from file_explorer.errors import DuplicateName, InvalidName, QuotaExceeded
from file_explorer.limits import MAX_NAME_LENGTH
from file_explorer.names import clean, is_rejected
from file_explorer.services.tree import TreeService

MAX_DEPTH = 64


@pytest.fixture
def svc(session: Session) -> TreeService:
    return TreeService(session, MAX_DEPTH)


# ------------------------------------------------------- what is rejected


@pytest.mark.invariant
@pytest.mark.parametrize(
    ("label", "name"),
    [
        ("NUL", "doc\x00ument"),
        ("C0 control", "doc\x01ument"),
        ("tab is trimmed but not inside", "doc\tument"),
        ("DEL", "doc\x7fument"),
        ("C1 control", "doc\x85ument"),
        ("RTL override", "exe.t\u202egpj.txt"),
        ("LTR override", "doc\u202dument"),
        ("pop directional formatting", "doc\u202cument"),
        ("first strong isolate", "doc\u2068ument"),
        ("pop directional isolate", "doc\u2069ument"),
        ("zero width space", "doc\u200bument"),
        ("word joiner", "doc\u2060ument"),
        ("byte order mark", "doc\ufeffument"),
        # Gate 3 found every one of these passing the old hand-written list.
        ("soft hyphen", "doc\u00adument"),
        ("mongolian vowel separator", "doc\u180eument"),
        ("combining grapheme joiner", "doc\u034fument"),
        ("hangul filler", "doc\u3164ument"),
        ("hangul choseong filler", "doc\u115fument"),
        ("halfwidth hangul filler", "doc\uffa0ument"),
        ("line separator", "doc\u2028ument"),
        ("paragraph separator", "doc\u2029ument"),
        ("lone surrogate", "doc\ud800ument"),
    ],
)
def test_names_that_are_rejected(label: str, name: str) -> None:
    """Controls, directional overrides and pure invisibles.

    The override cases are the Trojan Source set: they reorder how the rest of
    the string renders, so a name can display as `exe.txt` and be something
    else. There is no legitimate use for one inside a filename.
    """
    with pytest.raises(InvalidName):
        clean(name)


@pytest.mark.invariant
@pytest.mark.parametrize(
    ("label", "name"),
    [
        ("empty", ""),
        ("ASCII space", " "),
        ("non-breaking space", "\u00a0"),
        ("ideographic space", "\u3000"),
        ("ogham space mark", "\u1680"),
        ("mixed whitespace", " \t\u00a0\n "),
    ],
)
def test_names_that_are_nothing_once_trimmed(label: str, name: str) -> None:
    """`str.strip()` knows the whole Unicode whitespace class.

    The database check only trims six ASCII characters, so a name of one
    non-breaking space is storable without this — it looks blank, sorts as
    though blank, and cannot be typed again.
    """
    with pytest.raises(InvalidName):
        clean(name)


@pytest.mark.invariant
def test_a_name_longer_than_the_column_is_rejected() -> None:
    assert clean("a" * MAX_NAME_LENGTH) == "a" * MAX_NAME_LENGTH
    with pytest.raises(InvalidName):
        clean("a" * (MAX_NAME_LENGTH + 1))


@pytest.mark.invariant
def test_the_length_is_measured_after_normalising() -> None:
    """NFC composes, so it can shorten a string below the limit.

    Measuring first would reject a name that fits once stored.
    """
    # Written as escapes, not as literal combining marks. The two spellings are
    # indistinguishable in a diff, a terminal and a code review, which is the
    # whole problem this test is about -- so the source says which is which.
    decomposed = "e\u0301" * MAX_NAME_LENGTH  # 510 code points, 255 after NFC
    assert len(decomposed) == MAX_NAME_LENGTH * 2
    assert len(clean(decomposed)) == MAX_NAME_LENGTH


@pytest.mark.invariant
def test_a_non_string_is_rejected_rather_than_coerced() -> None:
    for value in (None, 42, ["name"], {"name": "x"}):
        with pytest.raises(InvalidName):
            clean(value)


# -------------------------------------------------------- what is allowed


@pytest.mark.invariant
@pytest.mark.parametrize(
    ("label", "name"),
    [
        ("zero width non-joiner", "می\u200cخواهم"),
        ("zero width joiner", "क्\u200dष"),
        ("left-to-right mark", "file\u200e.txt"),
        ("right-to-left mark", "קובץ\u200f.txt"),
        ("arabic letter mark", "\u0645\u0644\u0641\u061c.txt"),
        ("emoji", "holiday \U0001f334.jpg"),
        ("CJK", "年度報告.pdf"),
        ("inner spaces", "quarterly report 2026.pdf"),
    ],
)
def test_names_that_must_keep_working(label: str, name: str) -> None:
    """The deliberate holes in the deny-list, each for a reason.

    ZWNJ and ZWJ are the difference between two spellings in Persian and
    several Indic scripts, not decoration. LRM and RLM resolve direction for a
    neighbouring character rather than reordering the string. Banning them
    would make ordinary names in those languages unrepresentable, which is a
    worse failure than the narrow spoofing they allow.
    """
    assert clean(name) == unicodedata.normalize("NFC", name)


@pytest.mark.invariant
def test_surrounding_whitespace_is_trimmed_not_rejected(svc: TreeService, alice: int) -> None:
    assert clean("  report.pdf  ") == "report.pdf"
    assert svc.create(alice, None, "  spaced  ", "folder")
    assert [str(r["name"]) for r in svc.list_roots(alice)[0]] == ["spaced"]


@pytest.mark.invariant
def test_the_rule_is_a_category_not_a_list() -> None:
    """Every format character is rejected unless it is one of five carve-outs.

    The first version enumerated three invisible characters with no stated
    selection rule, and review found `U+00AD`, `U+180E`, `U+061C`, `U+034F` and
    four Hangul fillers walking straight past it. A list has to be extended
    every time one comes up; a category does not.

    This asserts the rule across the whole code space rather than sampling it,
    so a carve-out added later has to be declared here too.
    """
    carved_out = {"\u200c", "\u200d", "\u200e", "\u200f", "\u061c"}

    leaked = [
        f"U+{cp:04X}"
        for cp in range(0x110000)
        if unicodedata.category(chr(cp)) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        and chr(cp) not in carved_out
        and not is_rejected(chr(cp))
    ]
    assert not leaked, f"invisible or control characters that still pass: {leaked[:20]}"

    broken = [c for c in carved_out if is_rejected(c)]
    assert not broken, (
        f"a carve-out stopped working: {[f'U+{ord(c):04X}' for c in broken]}. "
        "ZWNJ, ZWJ and the three directional marks are orthography in Persian, "
        "Arabic and Indic scripts, not decoration."
    )


# --------------------------------------------------- why NFC is the point


@pytest.mark.invariant
def test_two_spellings_of_one_name_collide_instead_of_coexisting(
    svc: TreeService, alice: int
) -> None:
    """The payoff for normalising at the edge.

    `café` precomposed and `café` decomposed are different byte strings that
    render identically. Stored raw they become two siblings nobody can tell
    apart — and the unique index cannot help, because it compares stored bytes.
    Normalising before the insert is what makes the second one a 409.
    """
    home = svc.create(alice, None, "home", "folder")
    precomposed = "caf\u00e9"  # LATIN SMALL LETTER E WITH ACUTE
    decomposed = "cafe\u0301"  # e + COMBINING ACUTE ACCENT
    assert precomposed != decomposed

    svc.create(alice, home, precomposed, "folder")
    with pytest.raises(DuplicateName):
        svc.create(alice, home, decomposed, "folder")


@pytest.mark.invariant
def test_rename_normalises_too(svc: TreeService, alice: int) -> None:
    """Both write paths, or the second one reintroduces what the first prevents."""
    home = svc.create(alice, None, "home", "folder")
    precomposed = "caf\u00e9"
    decomposed = "cafe\u0301"
    assert precomposed != decomposed, "the two spellings must differ or this proves nothing"

    svc.create(alice, home, precomposed, "folder")
    other = svc.create(alice, home, "other", "folder")

    with pytest.raises(DuplicateName):
        svc.rename(alice, other, decomposed)


@pytest.mark.invariant
def test_a_rejected_name_never_reaches_the_driver(svc: TreeService, alice: int) -> None:
    """NUL is the sharp case: PostgreSQL text cannot hold it at all.

    Without the check it surfaces as a driver `ValueError` from deep inside
    psycopg — not an `IntegrityError`, so `_translate` would never see it and
    it would escape as a 500 carrying a stack trace.
    """
    with pytest.raises(InvalidName):
        svc.create(alice, None, "doc\x00ument", "folder")


# ------------------------------------------------------------- the quota


@pytest.mark.invariant
def test_the_per_owner_ceiling_is_enforced(session: Session, alice: int) -> None:
    svc = TreeService(session, MAX_DEPTH, max_nodes=3)
    for n in range(3):
        svc.create(alice, None, f"root-{n}", "folder")

    with pytest.raises(QuotaExceeded):
        svc.create(alice, None, "one-too-many", "folder")


@pytest.mark.invariant
def test_the_ceiling_is_per_owner_not_global(
    session: Session, alice: int, bob: int, make_node: Callable[..., int]
) -> None:
    svc = TreeService(session, MAX_DEPTH, max_nodes=2)
    for n in range(2):
        make_node(bob, f"theirs-{n}")
    session.commit()

    svc.create(alice, None, "mine", "folder")  # bob's two must not count here


# ----------------------------------------------------- the config wiring


@pytest.mark.invariant
def test_the_service_takes_its_bounds_from_configuration(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate 1's "four config values are read by nothing", closed.

    Built through the factory rather than by hand, because by hand is exactly
    how the values stayed unread.
    """
    # monkeypatch, not assignment: the `app` fixture is session-scoped, so an
    # unrestored write here would hand 7 and 11 to every test that ran after.
    with app.app_context():
        monkeypatch.setitem(app.config, "MAX_TREE_DEPTH", 7)
        monkeypatch.setitem(app.config, "MAX_NODES_PER_USER", 11)
        built = tree_service()

    assert built._max_depth == 7, "MAX_TREE_DEPTH did not reach the service"
    assert built._max_nodes == 11, "MAX_NODES_PER_USER did not reach the service"


@pytest.mark.invariant
def test_the_timeouts_reach_the_connection() -> None:
    """The values and the connection string cannot drift apart.

    They did: `SQLALCHEMY_ENGINE_OPTIONS` re-read `os.environ` rather than the
    constants directly above it, so setting one in code changed the number and
    not the connection.
    """
    options = engine_options(
        {
            "POOL_SIZE": 1,
            "MAX_OVERFLOW": 0,
            "STATEMENT_TIMEOUT_MS": 1234,
            "LOCK_TIMEOUT_MS": 567,
            "IDLE_IN_TRANSACTION_TIMEOUT_MS": 8910,
        }
    )
    connect = str(options["connect_args"])
    assert "statement_timeout=1234" in connect
    assert "lock_timeout=567" in connect
    assert "idle_in_transaction_session_timeout=8910" in connect


@pytest.mark.invariant
def test_overriding_a_timeout_changes_the_connection() -> None:
    """A class attribute would freeze at import and this would not hold."""

    class Tight(Config):
        STATEMENT_TIMEOUT_MS = 4321

    built = create_app(Tight)
    options = built.config["SQLALCHEMY_ENGINE_OPTIONS"]
    assert "statement_timeout=4321" in str(options["connect_args"]), (
        "the engine options were built before the override, so configuration "
        "that looks wired is not"
    )
