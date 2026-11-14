"""Failing tests for the five faults review found in already-shipped code.

Written before the fixes, so CI records them red. Each names the register id
from `.state/agents/M3-GATE1-REGISTER.md`.

These exercise `TreeService._translate` and the schema directly rather than
through an endpoint, because there is no HTTP layer yet — and because the leak
in A-1 happens at the point of translation, before any handler could redact it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from file_explorer.errors import DuplicateName, OperationTimedOut, TreeError
from file_explorer.services.tree import RETRYABLE, TIMEOUT, TreeService

MAX_DEPTH = 64


@pytest.fixture
def svc(session: Session) -> TreeService:
    return TreeService(session, MAX_DEPTH)


class _Diag:
    def __init__(self, name: str | None) -> None:
        self.constraint_name = name


class _Orig(Exception):
    """Stands in for a psycopg error carrying diagnostics."""

    def __init__(self, name: str | None, sqlstate: str = "23514") -> None:
        super().__init__("duplicate key value violates unique constraint")
        self.diag = _Diag(name)
        self.sqlstate = sqlstate


def _integrity_error(constraint: str | None) -> IntegrityError:
    """An IntegrityError shaped like the real thing, statement and all."""
    return IntegrityError(
        statement="UPDATE nodes SET parent_id = %(new_parent_id)s WHERE id = %(node_id)s",
        params={"new_parent_id": 7, "node_id": 42, "owner_id": 3},
        orig=_Orig(constraint),
    )


# ---------------------------------------------------------------- A-1


@pytest.mark.invariant
def test_unmapped_constraint_does_not_leak_the_statement(svc: TreeService) -> None:
    """A-1. The fallthrough returns the raw IntegrityError.

    `str()` on one embeds `[SQL: ...]` and `[parameters: ...]` — the statement,
    the constraint, the owner_id and whatever the user submitted. Whatever the
    500 handler does with that, it has already escaped the service.
    """
    result = svc._translate(_integrity_error("some_constraint_added_later"))

    rendered = str(result)
    assert "[SQL:" not in rendered, f"the statement leaked: {rendered[:200]}"
    assert "parameters" not in rendered, f"the parameters leaked: {rendered[:200]}"
    assert isinstance(result, TreeError), (
        f"expected a domain error, got {type(result).__name__} — "
        "an unmapped constraint must not escape as a driver exception"
    )


@pytest.mark.invariant
def test_missing_constraint_name_does_not_leak(svc: TreeService) -> None:
    """A-1, the other half. Not every driver fills in diag.constraint_name."""
    result = svc._translate(_integrity_error(None))
    assert "[SQL:" not in str(result)
    assert isinstance(result, TreeError)


# ---------------------------------------------------------------- A-4


@pytest.mark.invariant
def test_blank_name_is_not_reported_as_a_duplicate(svc: TreeService) -> None:
    """A-4. `ck_nodes_name_not_blank` is mapped to DuplicateName.

    So submitting an empty name answers "that name is already used in this
    folder" — which is both wrong and confusing, and 409 is the wrong status
    for a payload that is invalid on its own terms.
    """
    result = svc._translate(_integrity_error("ck_nodes_name_not_blank"))

    assert not isinstance(result, DuplicateName), "a blank name is not a duplicate name"
    assert isinstance(result, TreeError)
    assert result.status == 422, (
        f"a payload that is wrong on its own terms is 422, not {result.status}"
    )


# ---------------------------------------------------------------- A-3


@pytest.mark.invariant
def test_statement_timeout_is_recognised(session: Session) -> None:
    """A-3. 57014 was neither retryable nor an IntegrityError.

    A cascading delete of a large subtree that exceeds `statement_timeout`
    reached the caller as an unhandled driver error, after the statement had
    already held locks for the whole timeout.

    The first version of this test asserted 57014 belonged in `RETRYABLE`.
    That was wrong, and the fix is the better design rather than the one the
    test demanded: a statement that exceeded five seconds will exceed five
    seconds again, so retrying burns the timeout twice and still fails.
    Recognised and mapped to 503 is the right answer.
    """
    assert TIMEOUT == "57014", "the timeout sqlstate must be recognised"
    assert TIMEOUT not in RETRYABLE, (
        "a statement timeout must not be retried -- a cascading delete that "
        "exceeded the timeout will exceed it again, so a retry burns it twice"
    )
    assert OperationTimedOut.status == 503, (
        "a timeout is 503, not 500: the request was valid and may succeed later"
    )


@pytest.mark.invariant
def test_statement_timeout_really_raises_57014(
    session: Session, alice: int, make_node: Callable[..., int]
) -> None:
    """A-3, proving the sqlstate rather than assuming it."""
    make_node(alice, "docs")
    session.commit()

    session.execute(text("SET LOCAL statement_timeout = 1"))
    with pytest.raises(OperationalError) as caught:
        session.execute(text("SELECT pg_sleep(1)"))
        session.flush()

    sqlstate = getattr(getattr(caught.value, "orig", None), "sqlstate", None)
    assert sqlstate == "57014", f"expected 57014, got {sqlstate}"
    session.rollback()


# ---------------------------------------------------------------- A-5


@pytest.mark.invariant
def test_constraint_names_in_the_docs_exist_in_the_database(session: Session) -> None:
    """A-5. The docs and the database disagree, and `_translate` matches by name.

    `docs/ARCHITECTURE.md` §3 names `ck_nodes_name_nonempty`; the migration
    creates `ck_nodes_name_not_blank`. Renaming the constraint to match the
    document would turn every blank-name rejection into a 500, because the
    mapping is keyed on the name.

    This test is the thing that stops a documentation edit breaking runtime
    behaviour.
    """
    docs = Path(__file__).resolve().parents[3] / "docs" / "ARCHITECTURE.md"
    named = set(re.findall(r"\b(ck_nodes_\w+|fk_nodes_\w+|uq_nodes_\w+)\b", docs.read_text()))
    assert named, "no constraint names found in ARCHITECTURE.md — has it moved?"

    actual = {
        r[0]
        for r in session.execute(
            text("SELECT conname FROM pg_constraint WHERE conrelid = 'nodes'::regclass")
        ).all()
    } | {
        r[0]
        for r in session.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'nodes'")
        ).all()
    }

    missing = sorted(named - actual)
    assert not missing, (
        f"ARCHITECTURE.md names constraints the database does not have: {missing}. "
        f"Present: {sorted(actual)}"
    )


@pytest.mark.invariant
def test_every_constraint_the_mapping_knows_about_exists(session: Session) -> None:
    """A-5, from the other direction.

    `_translate` branches on constraint names. A name it checks for that no
    longer exists is a dead branch, and the error it was meant to produce
    silently becomes the fallthrough.
    """
    source = (
        Path(__file__).resolve().parents[1] / "src" / "file_explorer" / "services" / "tree.py"
    ).read_text()
    referenced = set(re.findall(r'"(ck_nodes_\w+|fk_nodes_\w+|uq_nodes_\w+)"', source))
    assert referenced, "no constraint names found in tree.py — has _translate moved?"

    actual = {
        r[0]
        for r in session.execute(
            text("SELECT conname FROM pg_constraint WHERE conrelid = 'nodes'::regclass")
        ).all()
    } | {
        r[0]
        for r in session.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'nodes'")
        ).all()
    }

    dead = sorted(referenced - actual)
    assert not dead, f"_translate branches on constraints that do not exist: {dead}"
