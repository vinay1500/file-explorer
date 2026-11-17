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
from flask.testing import FlaskClient
from flask_migrate import upgrade
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.datastructures import Headers

from file_explorer.app import create_app
from file_explorer.auth import CSRF_HEADER, SESSION_COOKIE
from file_explorer.extensions import db
from file_explorer.services.sessions import SessionService

# `sessions` cascades from `users`, so truncating users alone would clear it --
# but listing it keeps the statement honest about what it empties, and a future
# table that does not cascade would otherwise be missed here silently.
TABLES = ("sessions", "nodes", "users")


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
def new_session() -> Iterator[Callable[..., Session]]:
    """Factory for sessions on their own connections.

    Each one is a separate connection to the same database, so two of them can
    hold conflicting row locks and one will really wait. That is the point of
    `test_concurrency.py` — a mocked session cannot block.
    """
    engines = []
    sessions = []

    def make(lock_timeout_ms: int = 4000, statement_timeout_ms: int = 8000) -> Session:
        """A session on its own connection.

        The timeouts are arguments because the retry tests need a lock timeout
        short enough to fire deliberately — 55P03 is the sqlstate the retry
        loop exists for, and it is unreachable with a four-second budget.
        """
        engine = create_engine(
            os.environ["DATABASE_URL"],
            # One connection each, and no pooling surprises between tests.
            pool_size=1,
            max_overflow=0,
            # A test that waits on a lock for longer than it meant to has
            # already failed; fail loudly instead of hanging the suite.
            connect_args={
                "options": (
                    f"-c lock_timeout={lock_timeout_ms} -c statement_timeout={statement_timeout_ms}"
                )
            },
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


# ---------------------------------------------------------------- signing in


class SignedInClient(FlaskClient):
    """A test client that carries a session, and the CSRF token with it.

    The header is attached automatically because otherwise every mutating
    request in the suite would have to remember it, and the ones that forgot
    would fail for a reason that looks like the thing under test. A test that
    is *about* the CSRF check clears `csrf_token` and gets a client that omits
    it.
    """

    csrf_token: str | None = None

    def open(self, *args: object, **kwargs: object) -> object:
        if self.csrf_token:
            headers = Headers(kwargs.get("headers") or {})
            if CSRF_HEADER not in headers:
                headers[CSRF_HEADER] = self.csrf_token
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def sign_in(session: Session) -> Callable[[FlaskClient, int], str]:
    """Open a real session for `user_id` and give the client its cookie.

    This replaces `app.config["DEV_ACTOR_ID"] = user_id`, which is how every
    HTTP test used to authenticate. That is not a cosmetic change: Gate 1
    found that keeping a config-based identity alive alongside a real one fails
    open whichever order the two are read in, and **the pressure to keep 201
    tests green was exactly what would have kept it alive.** So the setting is
    gone and the tests mint a session like a browser does.

    Returns the CSRF token, and attaches it to the client.
    """

    def _sign_in(client: FlaskClient, user_id: int) -> str:
        issued = SessionService(db.session).begin(user_id)
        db.session.commit()
        client.set_cookie(SESSION_COOKIE, issued.token)
        if isinstance(client, SignedInClient):
            client.csrf_token = issued.csrf_token
        return issued.csrf_token

    return _sign_in


@pytest.fixture
def client(app: Flask, alice: int, session: Session) -> Iterator[FlaskClient]:
    """A test client holding a real session for alice.

    It used to be `app.config["DEV_ACTOR_ID"] = alice`, defined in
    `test_api_contract.py`. That setting is gone, and it lives here now because
    every HTTP test in the suite wants the same thing: a client that is signed
    in the way a browser is.

    Gate 1's reasoning, kept where the change happened: a config-based identity
    alongside a real one fails open whichever order the two are read in, and
    the pressure to keep this very fixture working is what would have kept the
    config version alive.
    """
    app.test_client_class = SignedInClient
    with app.test_client() as test_client:
        # `session` is `db.session` inside the app context this fixture chain
        # already holds, so the row goes in the same transaction as `alice`.
        # Pushing a *nested* app context here gets a different scoped session
        # and the insert fails on the foreign key, because alice is not
        # committed yet -- which is how this was written the first time.
        issued = SessionService(session).begin(alice)
        session.commit()
        test_client.set_cookie(SESSION_COOKIE, issued.token)
        if isinstance(test_client, SignedInClient):
            test_client.csrf_token = issued.csrf_token
        yield test_client


# ---------------------------------------------------------------- data builders


# A well-formed argon2id hash that **no password matches**, and that is
# deliberate rather than a limitation.
#
# The suite creates users constantly and almost none of those tests cares about
# logging in. Hashing a real password for each would add a memory-hard
# computation per fixture for no coverage, so this is a structurally valid
# encoded hash at the parameters in `passwords.py` with a fixed salt and a
# digest that is not the digest of anything: the library parses it and every
# verification against it is a clean mismatch.
#
# A test that needs a working login hashes its own password in the test and
# passes the result here. That keeps the expensive call in the handful of tests
# that are about authentication.
#
# `make_user` requires a hash rather than letting the column default, which is
# the whole point. `password_hash` carried `server_default=""` until migration
# 0003, so this fixture used to insert rows no login path could ever
# authenticate -- and the login tests would then have been written against that
# population and passed while every real row answered 500. Gate 1 found it
# before any login code existed.
FIXTURE_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$"
    "c2FsdHNhbHRzYWx0c2FsdA$Qx6sB0nMOSSz0CqN8nUfsFYQkOaJ5cPGNSDJBzqfKPo"
)


@pytest.fixture
def make_user(session: Session) -> Callable[..., int]:
    def _make(email: str, password_hash: str = FIXTURE_HASH) -> int:
        return int(
            session.execute(
                text("INSERT INTO users (email, password_hash) VALUES (:e, :h) RETURNING id"),
                {"e": email, "h": password_hash},
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
def without_owner_fk(session: Session) -> Iterator[None]:
    """Drop the composite owner key for one test, then put it back.

    Some guards defend against states the composite key makes unreachable, so
    with the key in place those guards cannot fail and the tests that cover
    them would pass with the guard deleted. The honest way to test them is to
    remove the other protection and build the illegal state directly.

    The definition lives here rather than in each test: two copies of this DDL
    is how one of them quietly re-creates a *different* constraint and the
    suite keeps passing against a schema nobody meant.

    Teardown deletes every node first -- `ADD CONSTRAINT` validates existing
    rows, so the illegal state has to go before the key comes back, and it has
    to happen even when the test fails.
    """
    session.execute(text("ALTER TABLE nodes DROP CONSTRAINT fk_nodes_parent_same_owner"))
    # Committed immediately. Teardown has to roll back first -- a test that
    # failed mid-transaction leaves the session unusable otherwise -- and an
    # uncommitted DROP would be undone by that rollback, so the ADD below would
    # then fail with "constraint already exists" and take out every later test
    # with it. The visible failure would be somewhere else entirely.
    session.commit()
    try:
        yield
    finally:
        session.rollback()
        session.execute(text("DELETE FROM nodes"))
        session.commit()
        session.execute(
            text(
                "ALTER TABLE nodes ADD CONSTRAINT fk_nodes_parent_same_owner "
                "FOREIGN KEY (parent_id, owner_id) REFERENCES nodes (id, owner_id) "
                "ON DELETE CASCADE"
            )
        )
        session.commit()


@pytest.fixture
def alice(make_user: Callable[[str], int]) -> int:
    return make_user("alice@example.com")


@pytest.fixture
def bob(make_user: Callable[[str], int]) -> int:
    return make_user("bob@example.com")
