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

import pytest

ROOT = Path(__file__).resolve().parents[3]
TESTS = Path(__file__).resolve().parent
#: Every committed document that names tests or tools, not just the two in
#: `docs/`. The README was outside this tuple until 18 November, which is how
#: three ticked claims elsewhere came to name tests that do not exist -- the
#: guard was not wrong, its scope was, and nobody had said so out loud. The
#: README is the document most likely to be read and least likely to be
#: re-checked, so leaving it out was the wrong way round.
DOCS = (
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "docs" / "SECURITY.md",
    ROOT / "docs" / "ARCHITECTURE.md",
)

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
            # A `continue` rather than a nested `if`: ruff's SIM102 wants the two
            # conditions combined, and combined they are 106 characters.
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
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


@pytest.mark.invariant
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


@pytest.mark.invariant
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


#: Tools a committed document once presented as delivered, that are not.
#:
#: Each has to stay struck through, marked cut, or gone. The list is explicit
#: rather than derived: a scanner that guesses which bolded words in a prose
#: table are Python packages produces false positives, and this repository has
#: already backed one such checker out for exactly that reason. A guard that
#: disagrees with reality is worse than no guard. This one cannot be wrong
#: about the cases it knows, and is silent about everything else -- which is
#: the honest trade and is stated here so nobody mistakes it for coverage.
CUT_CLAIMS = {
    # Named in ARCHITECTURE.md's stack table as the logging solution from
    # 13 November. Never a dependency, implemented nowhere, scheduled in no
    # task. Section 19 of the teardown had been carrying "could not claim
    # anything about structured logging" for days while the document that
    # would be read first still advertised it.
    "structlog": "cut 18 Nov -- the standard library's logging is what runs",
}

#: A cut claim is allowed to appear if it is visibly marked as cut.
CUT_MARKERS = ("~~", "Claim cut", "claim cut", "not delivered", "cut rather than")


@pytest.mark.invariant
@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_no_document_still_advertises_a_claim_that_was_cut(doc: Path) -> None:
    """A document read before the code is the one that must not overstate it.

    The failure is not "we did not build it". Deciding not to build something
    is fine and happens three times in this project. The failure is a table
    row that still reads as a feature after the decision went the other way,
    because the row is what a reviewer sees and the decision is not.

    `structlog` sat in `ARCHITECTURE.md` for five days as the logging
    solution. The teardown's own "what you may claim" section listed it as
    *not* claimable over the same period. Two committed artefacts, opposite
    stories, and nothing compared them.
    """
    text = doc.read_text(encoding="utf-8")
    for claim, why in sorted(CUT_CLAIMS.items()):
        for line in text.splitlines():
            if claim not in line:
                continue
            assert any(marker in line for marker in CUT_MARKERS), (
                f"{doc.name} still presents {claim!r} as delivered ({why}). Strike it through or "
                f"say it was cut, or a reviewer reads the row as a feature that exists."
            )


@pytest.mark.invariant
def test_the_cut_claim_guard_would_notice(tmp_path: Path) -> None:
    """The guard above passes trivially if the claim is simply absent.

    So this proves it can fail: the same check, against a line that names a cut
    claim with no marker on it. Without this, deleting every mention of
    `structlog` would leave a green test that asserts nothing -- which is the
    vacuous shape M4 found five of.
    """
    claim = next(iter(CUT_CLAIMS))
    unmarked = f"| Logging | Flask default | **{claim}**, JSON, request-id bound | |"
    assert claim in unmarked
    assert not any(marker in unmarked for marker in CUT_MARKERS), (
        "the fixture line is already marked as cut, so this test cannot fail"
    )
    marked = f"| Logging | Flask default | ~~{claim}~~ -> stdlib logging | Claim cut |"
    assert any(marker in marked for marker in CUT_MARKERS), (
        "a correctly marked line is being read as an unmarked claim"
    )


@pytest.mark.invariant
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
