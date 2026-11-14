"""Test fixtures.

**Isolation strategy: truncate, not rollback.**

The usual trick is to wrap each test in a transaction and roll it back. That is
faster, and it is unusable here: `test_concurrency.py` needs two connections to
commit independently so that one can genuinely block on the other's row locks.
Inside a single outer transaction there is nothing to block on, and the tests
that matter most would pass vacuously.

So each test gets a real database and `TRUNCATE … RESTART IDENTITY CASCADE`
afterwards. `RESTART IDENTITY` matters: several tests assert on specific node
ids, and without it they would pass in isolation and fail in a suite.

Migrations run once per session rather than `create_all`, so the suite exercises
the same path production does — a migration that applies cleanly in CI but not
on a real deploy is a bad afternoon, and this is where it gets caught.

`DATABASE_URL` is expected to point at a throwaway database. The truncation
below is unconditional and does not ask.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import pytest
from flask import Flask
from flask_migrate import upgrade
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from file_explorer.app import create_app
from file_explorer.extensions import db

TABLES = ("nodes", "users")


def _require_throwaway_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.exit("DATABASE_URL is not set; the suite needs a throwaway database", 1)
    # Thin guard, not real safety. It would not stop a determined mistake, but
    # it does stop the obvious one.
    if "prod" in url or "production" in url:
        pytest.exit("DATABASE_URL looks like a production database; refusing to truncate", 1)
    return url


@pytest.fixture(scope="session")
def app() -> Iterator[Flask]:
    _require_throwaway_url()
    application = create_app()
    with application.app_context():
        upgrade()
    yield application


@pytest.fixture(autouse=True)
def _clean(app: Flask) -> Iterator[None]:
    """Empty the tables after every test, whatever it did."""
    yield
    with app.app_context():
        db.session.rollback()
        db.session.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        db.session.commit()


@pytest.fixture
def session(app: Flask) -> Iterator[Session]:
    with app.app_context():
        yield db.session
        db.session.rollback()


@pytest.fixture
def new_session() -> Iterator[Callable[[], Session]]:
    """Factory for sessions on their own connections.

    Each one is a separate connection to the same database, so two of them can
    hold conflicting row locks and one will really wait. That is the point of
    `test_concurrency.py` — a mocked session cannot block.
    """
    engines = []
    sessions = []

    def make() -> Session:
        engine = create_engine(
            os.environ["DATABASE_URL"],
            # One connection each, and no pooling surprises between tests.
            pool_size=1,
            max_overflow=0,
            # A test that waits on a lock for more than a few seconds has
            # already failed; fail loudly instead of hanging the suite.
            connect_args={"options": "-c lock_timeout=4000 -c statement_timeout=8000"},
        )
        s = sessionmaker(bind=engine)()
        engines.append(engine)
        sessions.append(s)
        return s

    yield make

    for s in sessions:
        s.rollback()
        s.close()
    for e in engines:
        e.dispose()


# ---------------------------------------------------------------- data builders


@pytest.fixture
def make_user(session: Session) -> Callable[[str], int]:
    def _make(email: str) -> int:
        return int(
            session.execute(
                text("INSERT INTO users (email) VALUES (:e) RETURNING id"), {"e": email}
            ).scalar_one()
        )

    return _make


@pytest.fixture
def make_node(session: Session) -> Callable[..., int]:
    def _make(owner_id: int, name: str, parent_id: int | None = None, type_: str = "folder") -> int:
        return int(
            session.execute(
                text(
                    "INSERT INTO nodes (owner_id, parent_id, name, type) "
                    "VALUES (:o, :p, :n, :t) RETURNING id"
                ),
                {"o": owner_id, "p": parent_id, "n": name, "t": type_},
            ).scalar_one()
        )

    return _make


@pytest.fixture
def alice(make_user: Callable[[str], int]) -> int:
    return make_user("alice@example.com")


@pytest.fixture
def bob(make_user: Callable[[str], int]) -> int:
    return make_user("bob@example.com")
