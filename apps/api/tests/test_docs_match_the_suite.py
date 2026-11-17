"""The committed documents may not name a test that does not exist.

This is the cheapest guard in the project and it closes a whole class rather
than one instance of it.

`docs/ARCHITECTURE.md` §4 names a test against each of the eight invariants and
`docs/SECURITY.md` names one against most of its rules. Those names are the only
thing a reviewer can check quickly: they turn "we validate names" into "open
this file and run this one". A name that resolves to nothing is worse than no
name at all, because it reads as evidence and is not.

It has already gone wrong twice here. Seven statements in committed prose
described things that did not exist and were corrected on 14 November, and
M3b.5 was marked done with half of its title undelivered. Neither was caught by
a test, because nothing looked at the prose.

**Forward references are allowed, and must say so in the document.** A name
followed by `(planned` is read as intent rather than evidence, so a reviewer can
tell the two apart without running anything -- which is the whole point. The
marker is checked in both directions: a planned name that now exists fails too,
so the document cannot keep describing delivered work as future.

Collection is by AST rather than by invoking pytest. A subprocess inside the
suite is slow and inherits the argument parsing of whatever invoked it, and the
names of test functions are a static property of the files.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TESTS = Path(__file__).resolve().parent
DOCS = (ROOT / "docs" / "SECURITY.md", ROOT / "docs" / "ARCHITECTURE.md")

# A name in prose, in a table cell or in backticks.
NAME = re.compile(r"\btest_[a-z0-9_]+")

# How far after a name to look for the marker. One short parenthetical, so a
# marker belonging to the *next* name cannot be claimed by this one.
MARKER_WINDOW = 48
MARKER = "(planned"


def _collected() -> set[str]:
    """Every test function and test module the suite defines, read from source."""
    found: set[str] = set()
    for path in sorted(TESTS.glob("test_*.py")):
        found.add(path.stem)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name.startswith("test_"):
                    found.add(node.name)
    return found


def _named(doc: Path) -> tuple[set[str], set[str]]:
    """Return (claimed, planned) names from one document."""
    text = doc.read_text(encoding="utf-8")
    claimed: set[str] = set()
    planned: set[str] = set()
    for m in NAME.finditer(text):
        tail = text[m.end() : m.end() + MARKER_WINDOW]
        (planned if MARKER in tail else claimed).add(m.group())
    return claimed, planned


def test_every_test_named_in_the_documents_exists() -> None:
    """No document may name a test the suite does not define, unmarked.

    The fix for a failure here is **not** to write a test with whatever name the
    document happens to use. It is to decide which name is better and make both
    files agree -- usually by correcting the document, because a name chosen
    while writing a test says what it proves and a name chosen while writing
    prose says what the author wished it proved.
    """
    collected = _collected()
    missing: dict[str, list[str]] = {}
    for doc in DOCS:
        claimed, _ = _named(doc)
        gap = sorted(claimed - collected)
        if gap:
            missing[doc.name] = gap

    detail = "; ".join(f"{d} -> {', '.join(n)}" for d, n in sorted(missing.items()))
    assert not missing, (
        f"these are named in a document, do not exist, and are not marked `{MARKER}`: {detail}"
    )


def test_nothing_marked_planned_has_already_arrived() -> None:
    """The marker has to be removed when the test lands.

    Without this the allowance rots the other way: a document goes on calling
    delivered work "planned" and a reviewer skips the one name that is real.
    """
    collected = _collected()
    arrived: dict[str, list[str]] = {}
    for doc in DOCS:
        _, planned = _named(doc)
        now = sorted(planned & collected)
        if now:
            arrived[doc.name] = now

    detail = "; ".join(f"{d} -> {', '.join(n)}" for d, n in sorted(arrived.items()))
    assert not arrived, (
        f"a document still marks these `{MARKER}` and they exist now -- drop the marker: {detail}"
    )


def test_the_guard_can_see_the_suite_it_is_guarding() -> None:
    """A guard that collects nothing would pass while proving nothing.

    Both halves of each comparison have to be non-empty for the assertions above
    to mean anything, and a refactor that moved the tests or renamed a document
    would otherwise make this whole file silently vacuous -- which is the defect
    class four of M4's tests turned out to have.
    """
    collected = _collected()
    assert len(collected) > 150, f"only {len(collected)} names collected; the AST walk is broken"
    assert test_every_test_named_in_the_documents_exists.__name__ in collected

    for doc in DOCS:
        assert doc.is_file(), f"{doc} is not where this test expects it"
        claimed, planned = _named(doc)
        assert claimed or planned, f"{doc.name} names no tests at all, which it used to"
