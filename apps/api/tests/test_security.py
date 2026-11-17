"""The session, end to end, and the things it must refuse.

`docs/SECURITY.md` §2.5 and §2.6 named three of these by hand before any of it
existed. They are implemented under those names so the document and the suite
say the same thing -- which `test_every_test_named_in_the_documents_exists`
enforces.

Most of this file is M5's Gate 1 findings turned into assertions. The rule the
M2 gate produced is that a finding without a test is a finding that comes back,
so each one that could be written as a test is one.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

from file_explorer import passwords
from file_explorer.auth import CSRF_HEADER, SESSION_COOKIE
from file_explorer.services.sessions import IDLE_LIFETIME, SessionService

PASSWORD = "a long enough passphrase"


@pytest.fixture
def registered(session: OrmSession) -> Callable[[str], int]:
    """A user with a password that actually verifies.

    The shared `make_user` fixture stores a hash nothing matches, on purpose --
    the suite creates users constantly and almost none of them signs in. These
    tests are the exception, so they pay for a real hash.
    """

    def _make(email: str, password: str = PASSWORD) -> int:
        return int(
            session.execute(
                text("INSERT INTO users (email, password_hash) VALUES (:e, :h) RETURNING id"),
                {"e": email, "h": passwords.hash_password(password)},
            ).scalar_one()
        )

    return _make


@pytest.fixture
def anon(app: Flask, session: OrmSession) -> FlaskClient:
    """A client with no session at all."""
    return app.test_client()


# --- registration and login say the same thing about the same facts -------


@pytest.mark.invariant
def test_login_does_not_enumerate_users(
    anon: FlaskClient, registered: Callable[..., int], session: OrmSession
) -> None:
    """SECURITY §2.6. The two failures are indistinguishable in the response.

    Status, content type and body all match. The timing claim is handled by
    `test_login_hashes_even_when_no_user_exists`, which asserts the mechanism
    rather than measuring a clock -- a timing assertion on a shared CI runner
    measures the runner.
    """
    registered("known@example.com")
    session.commit()

    unknown = anon.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )
    wrong = anon.post(
        "/api/v1/auth/login", json={"email": "known@example.com", "password": "wrong password!!"}
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.content_type == wrong.content_type
    assert unknown.get_json() == wrong.get_json()
    assert SESSION_COOKIE not in unknown.headers.get("Set-Cookie", "")


@pytest.mark.invariant
def test_login_hashes_even_when_no_user_exists(
    anon: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dummy verification is what makes the two paths cost the same.

    Asserted by counting calls rather than by timing. The obvious
    implementation returns the moment the row is missing, which is a tenth of
    the work and a usable oracle on any connection quiet enough to measure.
    """
    calls: list[str] = []
    original = passwords.verify_password

    def counting(stored: str, password: str) -> bool:
        calls.append(stored)
        return original(stored, password)

    monkeypatch.setattr("file_explorer.blueprints.auth.passwords.verify_password", counting)

    anon.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})

    assert len(calls) == 1, "login returned without hashing, so the unknown path is faster"
    assert calls[0].startswith("$argon2id$")


@pytest.mark.invariant
def test_registering_a_taken_address_says_nothing(
    anon: FlaskClient, registered: Callable[..., int], session: OrmSession
) -> None:
    """A 409 here is a working account oracle that needs no session at all."""
    registered("taken@example.com")
    session.commit()

    fresh = anon.post(
        "/api/v1/auth/register", json={"email": "new@example.com", "password": PASSWORD}
    )
    taken = anon.post(
        "/api/v1/auth/register", json={"email": "taken@example.com", "password": PASSWORD}
    )

    assert fresh.status_code == taken.status_code == 202
    assert fresh.get_json() == taken.get_json()


@pytest.mark.invariant
def test_registration_normalises_the_address_before_storing_it(
    anon: FlaskClient, session: OrmSession
) -> None:
    """One spelling per address, decided in Python.

    `lower()` does not mean the same thing on the deployed database as it does
    in CI -- builtin `C.UTF-8` folds Unicode, libc `C` folds ASCII -- so the
    unique index cannot be the only thing deciding whether two addresses are
    one account.
    """
    for spelling in ("  Mixed.Case@Example.COM  ", "mixed.case@example.com"):
        anon.post("/api/v1/auth/register", json={"email": spelling, "password": PASSWORD})

    stored = session.execute(text("SELECT email FROM users")).scalars().all()
    assert stored == ["mixed.case@example.com"], f"stored {stored}"


# --- the session itself ---------------------------------------------------


@pytest.mark.invariant
def test_cookie_flags_set(
    anon: FlaskClient, registered: Callable[..., int], session: OrmSession
) -> None:
    """SECURITY §2.5. httpOnly is not configurable and must not become so."""
    registered("flags@example.com")
    session.commit()

    response = anon.post(
        "/api/v1/auth/login", json={"email": "flags@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200

    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Path=/" in cookie

    body = response.get_json()
    assert "csrf_token" in body
    assert body["csrf_token"] not in cookie, "the CSRF token must not ride in the session cookie"


@pytest.mark.invariant
def test_the_session_token_is_never_in_a_response_body(
    anon: FlaskClient, registered: Callable[..., int], session: OrmSession
) -> None:
    registered("body@example.com")
    session.commit()
    response = anon.post(
        "/api/v1/auth/login", json={"email": "body@example.com", "password": PASSWORD}
    )
    token = response.headers["Set-Cookie"].split("=", 1)[1].split(";", 1)[0]
    assert token not in response.get_data(as_text=True)


@pytest.mark.invariant
def test_a_request_with_no_session_is_401_before_it_is_422(anon: FlaskClient) -> None:
    """Authentication runs in `before_request`, ahead of body validation.

    `@blp.arguments` is the outermost decorator, so webargs used to validate
    first and an unauthenticated caller with a nonsense body got a 422 that
    described the schema they were not allowed to use.
    """
    response = anon.post("/api/v1/nodes", json={"nonsense": True})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Cookie")


@pytest.mark.invariant
def test_logout_revokes_the_row_not_just_the_cookie(
    client: FlaskClient, session: OrmSession
) -> None:
    """The sentence a stateless token cannot say.

    Clearing a cookie asks a browser to forget a credential. This asserts the
    row is gone, which is what makes a copy taken out of the browser useless
    too -- so the assertion deliberately keeps using the same cookie after
    logout rather than trusting that the client dropped it.
    """
    assert session.execute(text("SELECT count(*) FROM sessions")).scalar_one() == 1

    assert client.post("/api/v1/auth/logout").status_code == 204
    assert session.execute(text("SELECT count(*) FROM sessions")).scalar_one() == 0

    assert client.get("/api/v1/roots").status_code == 401


@pytest.mark.invariant
def test_me_returns_the_actor_and_the_token_to_echo(client: FlaskClient, alice: int) -> None:
    """A reloaded tab kept the cookie and lost the in-memory CSRF token."""
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] == alice
    assert body["csrf_token"]


# --- CSRF -----------------------------------------------------------------


@pytest.mark.invariant
def test_mutation_without_csrf_token_rejected(client: FlaskClient) -> None:
    """SECURITY §2.5, and Gate 1's finding about what "double submit" meant.

    The token is compared against the value in the **session row**, so it is a
    synchronizer token rather than a double-submit one. The difference matters:
    double-submit compares the request to itself, so anyone who can set a
    cookie for the site can satisfy it.
    """
    client.csrf_token = None  # type: ignore[attr-defined]
    response = client.post("/api/v1/nodes", json={"parent_id": None, "name": "x", "type": "folder"})
    assert response.status_code == 403


@pytest.mark.invariant
def test_a_wrong_csrf_token_is_refused(client: FlaskClient) -> None:
    response = client.post(
        "/api/v1/nodes",
        json={"parent_id": None, "name": "x", "type": "folder"},
        headers={CSRF_HEADER: "not the right token"},
    )
    assert response.status_code == 403


@pytest.mark.invariant
def test_another_sessions_csrf_token_does_not_work(
    client: FlaskClient, session: OrmSession, bob: int
) -> None:
    """Bound to the session, not merely well-formed.

    This is the assertion that separates a synchronizer token from a
    double-submit one: a real, live, correctly-shaped token belonging to a
    different session is refused.
    """
    other = SessionService(session).begin(bob)
    session.commit()

    response = client.post(
        "/api/v1/nodes",
        json={"parent_id": None, "name": "x", "type": "folder"},
        headers={CSRF_HEADER: other.csrf_token},
    )
    assert response.status_code == 403


@pytest.mark.invariant
def test_a_safe_method_needs_no_csrf_token(client: FlaskClient) -> None:
    client.csrf_token = None  # type: ignore[attr-defined]
    assert client.get("/api/v1/roots").status_code == 200


# --- expiry, revocation and the sweeper -----------------------------------


@pytest.mark.invariant
def test_an_expired_session_is_401_and_is_deleted(client: FlaskClient, session: OrmSession) -> None:
    """Expiry is checked in Python so the row can be removed when it is met.

    A `WHERE expires_at > now()` would hide it and leave it to accumulate.
    """
    # `created_at` moves too: `ck_sessions_expiry_after_creation` forbids a row
    # that expires before it begins, which is the constraint doing its job on
    # the test that forgot about it.
    session.execute(
        text(
            "UPDATE sessions SET created_at = now() - interval '2 days', "
            "expires_at = now() - interval '1 second'"
        )
    )
    session.commit()

    assert client.get("/api/v1/roots").status_code == 401
    assert session.execute(text("SELECT count(*) FROM sessions")).scalar_one() == 0


@pytest.mark.invariant
def test_an_idle_session_expires_even_though_it_has_not_aged_out(
    client: FlaskClient, session: OrmSession
) -> None:
    stale = datetime.now(UTC) - IDLE_LIFETIME - timedelta(minutes=1)
    session.execute(text("UPDATE sessions SET last_seen_at = :t"), {"t": stale})
    session.commit()

    assert client.get("/api/v1/roots").status_code == 401


@pytest.mark.invariant
def test_using_a_session_moves_last_seen_at(client: FlaskClient, session: OrmSession) -> None:
    before = datetime.now(UTC) - timedelta(minutes=30)
    session.execute(text("UPDATE sessions SET last_seen_at = :t"), {"t": before})
    session.commit()

    assert client.get("/api/v1/roots").status_code == 200

    after = session.execute(text("SELECT last_seen_at FROM sessions")).scalar_one()
    assert after > before, "an idle timeout that never moves is a fixed timeout"


@pytest.mark.invariant
def test_revoke_all_ends_every_session_for_one_user(
    session: OrmSession, alice: int, bob: int
) -> None:
    """What a password change has to do."""
    service = SessionService(session)
    for _ in range(3):
        service.begin(alice)
    service.begin(bob)
    session.commit()

    assert service.revoke_all(alice) == 3
    session.commit()

    left = session.execute(text("SELECT user_id FROM sessions")).scalars().all()
    assert left == [bob]


@pytest.mark.invariant
def test_revoking_an_unknown_token_is_not_an_error(session: OrmSession) -> None:
    assert SessionService(session).revoke("no such token") == 0
    assert SessionService(session).revoke("") == 0


@pytest.mark.invariant
def test_resolving_nothing_is_none_rather_than_a_crash(session: OrmSession) -> None:
    service = SessionService(session)
    assert service.resolve("") is None
    assert service.resolve("not a token anyone issued") is None


@pytest.mark.invariant
def test_purge_expired_removes_what_nobody_came_back_for(session: OrmSession, alice: int) -> None:
    """`resolve` cleans up a row it happens to meet. Most are never met again."""
    service = SessionService(session)
    service.begin(alice)
    service.begin(alice, lifetime=timedelta(seconds=1))
    session.commit()

    removed = service.purge_expired(now=datetime.now(UTC) + timedelta(seconds=2))
    session.commit()

    assert removed == 1
    assert session.execute(text("SELECT count(*) FROM sessions")).scalar_one() == 1


# --- isolation ------------------------------------------------------------


@pytest.mark.invariant
def test_a_session_only_ever_sees_its_own_owners_tree(
    app: Flask, session: OrmSession, alice: int, bob: int, make_node: Callable[..., int]
) -> None:
    """Two users, two trees, no path between them -- through real sessions now.

    Until this milestone the same property was asserted with a configured
    actor, which proved the queries were scoped and said nothing about whether
    a credential could reach the wrong one.
    """
    make_node(alice, "alice-root")
    bobs = make_node(bob, "bob-root")
    issued = SessionService(session).begin(alice)
    session.commit()

    as_alice = app.test_client()
    as_alice.set_cookie(SESSION_COOKIE, issued.token)

    roots = as_alice.get("/api/v1/roots").get_json()
    assert [n["name"] for n in roots["items"]] == ["alice-root"]
    # `/nodes/{id}` carries PATCH and DELETE, not GET, so the read that proves
    # isolation is the breadcrumb walk.
    assert as_alice.get(f"/api/v1/nodes/{bobs}/path").status_code == 404


# --- the two guards that should never fire --------------------------------


@pytest.mark.invariant
def test_require_actor_fails_closed_if_the_hook_never_ran(app: Flask) -> None:
    """`authenticate` always runs, and this is what happens when it has not.

    Not a reachable state through HTTP -- it is a `before_request`, so there is
    no route it does not cover. The branch exists because the alternative when
    someone later adds a route outside the normal path is an `AttributeError`
    and a 500, and a 401 is the honest answer to "nobody established who this
    is". Coverage found it unexercised, which is exactly the kind of line worth
    exercising: the one that only runs when something else has already gone
    wrong.
    """
    from werkzeug.exceptions import Unauthorized

    from file_explorer.auth import current_csrf_token, require_actor

    with app.test_request_context("/api/v1/roots"):
        with pytest.raises(Unauthorized):
            require_actor()
        with pytest.raises(Unauthorized):
            current_csrf_token()


@pytest.mark.invariant
def test_a_weaker_stored_hash_is_upgraded_on_a_successful_login(
    anon: FlaskClient, session: OrmSession
) -> None:
    """An encoded hash carries its own parameters, so raising the cost is safe.

    Old hashes keep verifying and are rewritten at the current cost the next
    time the password is in hand -- which is the only moment it ever is.
    """
    from argon2 import PasswordHasher

    weak = PasswordHasher(memory_cost=8, time_cost=1, parallelism=1).hash(PASSWORD)
    session.execute(
        text("INSERT INTO users (email, password_hash) VALUES (:e, :h)"),
        {"e": "weak@example.com", "h": weak},
    )
    session.commit()

    response = anon.post(
        "/api/v1/auth/login", json={"email": "weak@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200

    stored = session.execute(text("SELECT password_hash FROM users")).scalar_one()
    assert stored != weak, "a weaker hash survived a successful login"
    assert f"m={passwords.MEMORY_COST_KIB}" in stored
