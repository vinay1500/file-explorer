"""The retry branch.

Review found this had zero coverage: roughly twenty-five lines at the centre
of the concurrency design, never executed by any test. The four-cycle test
releases its lock after 0.6 s against a 4 s `lock_timeout`, so it never waits
long enough to enter the branch at all.

Deterministic rather than timed. The contending transaction is released when
the service has actually logged a retry, not after a hopeful sleep — so these
do not depend on how long a backoff happens to take.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator

import pytest
from flask import Flask
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from file_explorer.errors import OperationTimedOut
from file_explorer.services.tree import RETRYABLE, TIMEOUT, TreeService

MAX_DEPTH = 64
SERVICE_LOGGER = "file_explorer.services.tree"


@pytest.fixture
def retry_log() -> Iterator[list[logging.LogRecord]]:
    """Capture the service's own records.

    A plain handler rather than `caplog`: the service runs on a worker thread
    here, and reading the records as they arrive is what makes the release
    deterministic.
    """
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect()
    log = logging.getLogger(SERVICE_LOGGER)
    log.addHandler(handler)
    previous = log.level
    log.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        log.removeHandler(handler)
        log.setLevel(previous)


def _retries(records: list[logging.LogRecord]) -> int:
    return sum(1 for r in records if "contention" in r.getMessage())


def _wait_for(predicate: Callable[[], bool], seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.invariant
def test_running_migrations_does_not_silence_application_logging(app: Flask) -> None:
    """Migrations must not turn the application's logging off.

    `logging.config.fileConfig` defaults to `disable_existing_loggers=True`, so
    it sets `.disabled` on every logger `alembic.ini` does not name. Alembic
    names root, `sqlalchemy.engine` and `alembic`; it does not name
    `file_explorer.*` or `flask.app`. A disabled logger drops records inside
    `isEnabledFor`, before any handler is consulted -- so there is no warning
    that logging has stopped, and nothing downstream to notice.

    In this suite the symptom was two tests below failing: the retry loop ran
    correctly and its warning was swallowed. In production the symptom is worse
    and quieter -- an in-process `flask db upgrade` leaves the process with no
    contention warnings and no health-check failures for the rest of its life.

    Requesting `app` is what makes this a test: that fixture runs `upgrade()`.
    """
    registry = logging.Logger.manager.loggerDict

    # Read the registry rather than calling getLogger: getLogger CREATES a
    # logger that is absent, freshly enabled, so a test written that way passes
    # whatever the migration did. This assertion is what stops that -- if the
    # service logger is not registered by now, the import moved and the scan
    # below has nothing left to check.
    assert SERVICE_LOGGER in registry, (
        f"{SERVICE_LOGGER} is not registered, so this test proves nothing"
    )

    silenced = sorted(
        name
        for name, logger in list(registry.items())
        if isinstance(logger, logging.Logger)
        and logger.disabled
        and (name.startswith("file_explorer") or name.startswith("flask"))
    )
    assert not silenced, (
        f"migrations disabled these loggers: {silenced}. "
        "migrations/env.py must pass disable_existing_loggers=False to fileConfig"
    )


@pytest.mark.concurrency
def test_lock_timeout_is_retried_and_then_succeeds(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[..., Session],
    retry_log: list[logging.LogRecord],
) -> None:
    """55P03 is the realistic contention failure, and it must recover.

    With `lock_timeout` at three seconds in production, a chain held by a slow
    transaction surfaces as a lock timeout long before the deterministic lock
    order could produce a deadlock. So this is the path the retry loop mostly
    exists for, and it had no test.
    """
    home = make_node(alice, "home")
    sub = make_node(alice, "sub", parent_id=home)
    mover = make_node(alice, "mover")
    session.commit()

    holder = new_session()
    # 200 ms, not the 60 ms the bounded test uses. Three attempts plus backoff
    # is the actor's whole budget before it gives up, and the holder has to be
    # scheduled, wake from the Event and complete a ROLLBACK round-trip inside
    # it. At 60 ms that budget is ~260 ms end to end, which is thin on a loaded
    # runner; at 200 ms there is roughly half a second of slack.
    actor = new_session(lock_timeout_ms=200)
    svc = TreeService(actor, MAX_DEPTH)

    release = threading.Event()
    outcome: dict[str, object] = {}

    def hold() -> None:
        # Lock the chain the mover needs, then wait to be let go.
        TreeService(holder, MAX_DEPTH)._repo.lock_move_participants(alice, sub, home)
        release.wait(timeout=10)
        holder.rollback()

    def attempt() -> None:
        try:
            svc.move(alice, mover, sub)
            actor.commit()
            outcome["result"] = "moved"
        except Exception as exc:
            actor.rollback()
            outcome["result"] = f"{type(exc).__name__}"

    t_hold = threading.Thread(target=hold, daemon=True)
    t_hold.start()
    time.sleep(0.1)  # let the holder take its locks before contending

    t_move = threading.Thread(target=attempt, daemon=True)
    t_move.start()

    # Release only once the service has genuinely retried. If it never logs,
    # the branch was not entered and the test has proved nothing.
    assert _wait_for(lambda: _retries(retry_log) >= 1), (
        "the service never logged a retry — the lock timeout did not reach the "
        "retry branch"
    )
    release.set()
    t_hold.join(timeout=10)
    t_move.join(timeout=15)

    assert outcome.get("result") == "moved", (
        f"the move should have succeeded on a later attempt; got {outcome.get('result')}"
    )
    assert _retries(retry_log) >= 1
    assert (
        session.execute(
            text("SELECT parent_id FROM nodes WHERE id = :i"), {"i": mover}
        ).scalar_one()
        == sub
    )


@pytest.mark.concurrency
def test_retries_are_bounded_and_then_the_error_escapes(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[..., Session],
    retry_log: list[logging.LogRecord],
) -> None:
    """A retry loop that never gives up is a hang, not a retry."""
    home = make_node(alice, "home")
    sub = make_node(alice, "sub", parent_id=home)
    mover = make_node(alice, "mover")
    session.commit()

    holder = new_session()
    actor = new_session(lock_timeout_ms=60)
    svc = TreeService(actor, MAX_DEPTH)

    TreeService(holder, MAX_DEPTH)._repo.lock_move_participants(alice, sub, home)
    try:
        with pytest.raises(DBAPIError) as caught:
            svc.move(alice, mover, sub)
        actor.rollback()

        sqlstate = getattr(getattr(caught.value, "orig", None), "sqlstate", None)
        assert sqlstate in RETRYABLE, f"expected a retryable sqlstate, got {sqlstate}"
        # Three attempts means two retries logged before the third gives up.
        assert _retries(retry_log) == 2, (
            f"expected 2 retries before giving up, saw {_retries(retry_log)}"
        )
    finally:
        holder.rollback()

    assert (
        session.execute(
            text("SELECT parent_id FROM nodes WHERE id = :i"), {"i": mover}
        ).scalar_one()
        is None
    ), "the move must not have landed"


@pytest.mark.concurrency
def test_statement_timeout_becomes_503_and_is_not_retried(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[..., Session],
    retry_log: list[logging.LogRecord],
) -> None:
    """57014 is recognised, mapped, and deliberately not retried.

    A statement that exceeded its budget will exceed it again, so a retry
    burns the timeout twice and still fails.
    """
    a = make_node(alice, "a")
    b = make_node(alice, "b")
    session.commit()

    # One millisecond: every statement the move issues will exceed it.
    actor = new_session(statement_timeout_ms=1)
    svc = TreeService(actor, MAX_DEPTH)

    with pytest.raises(OperationTimedOut):
        svc.move(alice, b, a)
    actor.rollback()

    assert _retries(retry_log) == 0, (
        "a statement timeout must not be retried; "
        f"{_retries(retry_log)} retry/retries were logged"
    )
    assert TIMEOUT not in RETRYABLE


@pytest.mark.concurrency
def test_a_non_retryable_database_error_propagates_unchanged(
    session: Session,
    alice: int,
    make_node: Callable[..., int],
    new_session: Callable[..., Session],
    retry_log: list[logging.LogRecord],
) -> None:
    """Only the listed sqlstates retry. Everything else escapes as itself.

    A retry loop that swallows unrelated failures turns a bug into a hang.
    """
    a = make_node(alice, "a")
    b = make_node(alice, "b")
    session.commit()

    actor = new_session()
    svc = TreeService(actor, MAX_DEPTH)

    # 22003 numeric_value_out_of_range — not retryable, not an IntegrityError.
    def explode(*_: object, **__: object) -> None:
        actor.execute(text("SELECT 1/0"))

    svc._repo.lock_move_participants = explode  # type: ignore[method-assign]

    with pytest.raises(DBAPIError) as caught:
        svc.move(alice, b, a)
    actor.rollback()

    sqlstate = getattr(getattr(caught.value, "orig", None), "sqlstate", None)
    assert sqlstate not in RETRYABLE
    assert sqlstate != TIMEOUT
    assert _retries(retry_log) == 0, "a non-retryable error must not be retried"
