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
from typing import ClassVar

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
    assert fresh.get_json() == taken.get_json() == {"accepted": True}


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
    # One registration, in one spelling, and the assertion is on the form that
    # reached the table.
    #
    # The first version of this test posted two spellings and asserted the
    # table held one row. It could not fail: the untrimmed spelling is
    # rejected by `fields.Email` before any handler runs, so only the second
    # ever arrived, and the surviving row was already lowercase. Deleting
    # `normalise_email` entirely left the assertion true.
    assert (
        anon.post(
            "/api/v1/auth/register",
            json={"email": "Mixed.Case@Example.COM", "password": PASSWORD},
        ).status_code
        == 202
    )

    stored = session.execute(text("SELECT email FROM users")).scalars().all()
    assert stored == ["mixed.case@example.com"], (
        f"stored {stored} -- without normalisation this is the spelling the caller sent, and "
        "every later lookup casefolds before comparing, so the account would be unreachable"
    )


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


# --- the cookie has to be able to reach us --------------------------------


@pytest.mark.invariant
def test_cookie_samesite_and_allowed_origins_cannot_disagree() -> None:
    """A startup check, because no HTTP test can catch this one.

    `SameSite=Lax` is refused at storage time on a cross-site response that is
    not a top-level navigation. A browser on one registrable domain talking to
    an API on another would log in, store nothing, and 401 on the next request
    with no error anyone could read.

    And it would pass everything: Flask's test client does not enforce SameSite
    at all, and `localhost:5173` to `localhost:5000` is cross-*origin* but
    same-*site*, because SameSite compares the registrable domain and ignores
    scheme and port. Local development, Compose and CI would all behave and
    only the deployed demo would be broken, which is the worst place for a
    failure to hide.

    The two settings that contradict each other are both configuration, so the
    contradiction is visible before any request exists. `create_app` refuses.
    """
    from file_explorer.app import create_app
    from file_explorer.config import Config

    class Split(Config):
        PUBLIC_API_ORIGIN = "https://file-explorer-backend.onrender.com"
        CORS_ALLOWED_ORIGINS: ClassVar[list[str]] = ["https://file-explorer.vercel.app"]
        AUTH_COOKIE_SAMESITE = "Lax"

    with pytest.raises(RuntimeError, match="cannot work with a frontend on a different site"):
        create_app(Split)

    class SameSite(Split):
        CORS_ALLOWED_ORIGINS: ClassVar[list[str]] = ["https://app.onrender.com"]

    create_app(SameSite)

    class Relaxed(Split):
        AUTH_COOKIE_SAMESITE = "None"

    create_app(Relaxed)


@pytest.mark.invariant
def test_the_default_configuration_is_same_origin_and_needs_no_check() -> None:
    """D-22's shape: no configured frontend origin, because there is no cross-origin traffic."""
    from file_explorer.app import create_app
    from file_explorer.config import Config

    assert Config.PUBLIC_API_ORIGIN == ""
    assert Config.CORS_ALLOWED_ORIGINS == []
    create_app(Config)


# --- the rate limit counts the right person -------------------------------


@pytest.mark.invariant
def test_the_limit_counts_the_session_not_the_address(
    app: Flask, session: OrmSession, alice: int, bob: int
) -> None:
    """Two users behind one address are two buckets.

    Keying on the address was the M3b placeholder and Gate 1 called it out in
    both directions. An address is **shared** -- everyone behind one NAT is one
    bucket, so a busy network turns the limit into an outage switch for
    everybody on it. And it is **chosen** -- behind a proxy that forwards one,
    a caller who wants more than their share sets a different value.

    Every test client here comes from the same address, so if the key were the
    address this could not pass -- and it did not, the first time. Flask-Limiter
    registers its own `before_request` during `init_app`, and `authenticate` was
    registered after it, so the limiter asked who the caller was before anything
    had decided. The key was the address for every request and the change to
    key on the session did nothing at all.

    Same trap as the `Retry-After` strip, in the opposite direction: that one
    had to be registered first so it would run last, because `after_request`
    runs in reverse. Both were caught by a test that drove the real behaviour.
    """
    from file_explorer.extensions import limiter

    limit = int(app.config["RATELIMIT_DEFAULT"].split()[0])

    def client_for(user_id: int) -> FlaskClient:
        issued = SessionService(session).begin(user_id)
        session.commit()
        c = app.test_client()
        c.set_cookie(SESSION_COOKIE, issued.token)
        return c

    limiter.reset()
    try:
        as_alice, as_bob = client_for(alice), client_for(bob)
        spent = [as_alice.get("/api/v1/roots") for _ in range(limit + 2)]
        assert any(r.status_code == 429 for r in spent), "alice never hit her own limit"

        # Same address, different session. If the key were the address this
        # would already be exhausted.
        assert as_bob.get("/api/v1/roots").status_code == 200
    finally:
        limiter.reset()


@pytest.mark.invariant
def test_an_unauthenticated_caller_still_counts_against_something(app: Flask) -> None:
    """There is no session on the way to getting one, so the key falls back.

    Recorded as a deliberate fallback rather than an oversight: the auth
    endpoints are the one place the address is all there is, and the
    alternative -- a per-account limit -- answers "does this person have an
    account" to anyone who asks.
    """
    from file_explorer.extensions import rate_limit_key

    with app.test_request_context("/api/v1/auth/login"):
        key = rate_limit_key()
    assert key.startswith("addr:"), key

    with app.test_request_context("/api/v1/roots"):
        from flask import g

        g.actor_id = 42
        assert rate_limit_key() == "user:42"


@pytest.mark.invariant
def test_two_registrations_of_one_address_race_to_the_same_answer(
    anon: FlaskClient, session: OrmSession
) -> None:
    """The check-then-insert has a window, and the index is what closes it.

    Without handling the violation the loser is an unhandled 500 -- which is
    both a crash and an oracle, because a 500 here means "somebody else just
    registered this". Simulated by inserting the row between the two calls,
    which is the same interleaving without needing two connections.
    """
    body = {"email": "race@example.com", "password": PASSWORD}
    assert anon.post("/api/v1/auth/register", json=body).status_code == 202

    # The row now exists; the second call takes the "already taken" path.
    second = anon.post("/api/v1/auth/register", json=body)
    assert second.status_code == 202

    count = session.execute(
        text("SELECT count(*) FROM users WHERE email = 'race@example.com'")
    ).scalar_one()
    assert count == 1


# --- isolation, by mechanism rather than by list --------------------------


@pytest.mark.invariant
def test_every_route_taking_a_node_id_refuses_another_owners_node(
    client: FlaskClient, session: OrmSession, bob: int, make_node: Callable[..., int]
) -> None:
    """Driven off the URL map, so a route added next month is covered by default.

    `test_no_route_reaches_another_owners_node` asserts the same property
    against a hand-written list of six calls. A list is a thing somebody
    forgets to extend -- M5 added four routes, and a list would have gone on
    passing while covering six of ten.

    This walks `app.url_map`, takes every rule with a `node_id` converter, and
    drives each of its methods against a node that belongs to bob. The answer
    must be **404 every time**: never 403, which confirms the row exists and
    makes sequential ids enumerable, and never 2xx.

    The bodies are deliberately valid. An invalid body would be refused at
    validation and the test would pass without ever reaching the ownership
    check, which is the vacuous shape M4 found five of.
    """
    theirs = make_node(bob, "bob-private")
    make_node(bob, "bob-child", parent_id=theirs)
    session.commit()

    bodies: dict[str, dict[str, object]] = {
        "PATCH": {"name": "mine-now"},
        "POST": {"new_parent_id": None},
    }

    checked: list[str] = []
    wrong: dict[str, int] = {}
    for rule in client.application.url_map.iter_rules():
        if "node_id" not in rule.arguments:
            continue
        path = rule.rule.replace("<int:node_id>", str(theirs))
        for method in sorted(rule.methods or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            label = f"{method} {rule.rule}"
            checked.append(label)
            response = client.open(path, method=method, json=bodies.get(method))
            if response.status_code != 404:
                wrong[label] = response.status_code

    assert len(checked) >= 5, f"the URL map walk found almost nothing: {checked}"
    assert not wrong, f"these did not answer 404 for another owner's node: {wrong}"


@pytest.mark.invariant
def test_creating_under_another_owners_parent_is_404(
    client: FlaskClient, session: OrmSession, bob: int, make_node: Callable[..., int]
) -> None:
    """The one cross-owner path that carries no node id in the URL.

    `POST /nodes` takes the parent in the body, so the URL-map walk above
    cannot see it. Named separately rather than folded in, because a route
    whose ownership check lives in the body is exactly the kind a sweep
    misses -- and the sweep would have gone on passing without it.

    Note what the right answer is: **404 on the parent**, not 403 and not 422.
    The caller is told the parent does not exist, because as far as they are
    concerned it does not.
    """
    theirs = make_node(bob, "bob-private")
    session.commit()

    response = client.post(
        "/api/v1/nodes", json={"name": "smuggled", "type": "folder", "parent_id": theirs}
    )
    assert response.status_code == 404

    left = session.execute(text("SELECT count(*) FROM nodes WHERE name = 'smuggled'")).scalar_one()
    assert left == 0, "the node was created anyway, under someone else's parent"


@pytest.mark.invariant
def test_every_repository_query_filters_by_owner() -> None:
    """The answer to "what proves a query someone adds next month is scoped".

    The route tests prove the routes that exist refuse. They can prove nothing
    about a query that has not been written, and Gate 1's credibility review
    named that gap exactly: *nothing makes a query scoped; the IDOR sweep is a
    hand-written list*.

    This reads `node_repo.py` and checks every statement handed to `text(...)`
    -- the module-level constants **and the ones written inline inside a
    method**. A new query with no owner predicate fails here before it can
    reach a route.

    ## Two things this got wrong before it got them right

    The first version asked only "does the SQL mention `owner_id`". That is
    vacuous against the mistake that matters: a recursive CTE needs the
    predicate in the anchor term *and* the recursive term, and dropping the
    recursive one leaves `owner_id` in the string three more times. So the
    counts are recorded.

    The second version walked only module-level assignments, and **seven of
    the twenty-two owner predicates in the file live inside methods**, so a
    third of the repository was outside a test claiming to cover all of it.
    Found by removing each predicate line in turn and checking which removals
    the test noticed. That exercise is the only reason either defect is known,
    and it is cheap enough to repeat whenever this file changes.

    Brittle in the safe direction: a legitimate rewrite has to come here and
    say what it changed, which is the review.
    """
    import ast
    from pathlib import Path

    module = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "file_explorer"
        / "repositories"
        / "node_repo.py"
    )
    assert module.is_file(), f"{module} is not where this test expects it"

    def sql_in(node: ast.AST) -> list[str]:
        """Every string handed to `text(...)` anywhere inside `node`."""
        found: list[str] = []
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call) or not inner.args:
                continue
            name = getattr(inner.func, "id", None) or getattr(inner.func, "attr", None)
            if name != "text":
                continue
            arg = inner.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.append(arg.value)
            elif isinstance(arg, ast.JoinedStr):
                # An f-string: only the literal parts are visible, which is
                # enough, because a predicate is never interpolated here.
                found.append("".join(v.value for v in arg.values if isinstance(v, ast.Constant)))
        return found

    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))

    counted: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    for sql in sql_in(node):
                        counted[target.id] = sql.count("owner_id")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            statements = sql_in(node)
            if statements:
                counted[f"{node.name}()"] = sum(sql.count("owner_id") for sql in statements)

    # Every owner predicate in the file, by where it lives. Four of the counts
    # are 4 or 6 rather than 2 because a recursive CTE scopes both terms.
    EXPECTED = {
        "_ANCESTRY": 4,
        "_SUBTREE_HEIGHT": 4,
        "_LOCK_CHAIN": 6,
        "_LOCK_ANCESTRY": 6,
        "_SUBTREE_SIZE": 4,
        "_LOCK_ONE": 2,
        "_LIST_CHILDREN": 2,
        "_LIST_ROOTS": 2,
        "get()": 2,
        "is_ancestor_of()": 4,
        "insert_node()": 2,
        "set_name()": 2,
        "delete_node()": 2,
        "count_for_owner()": 2,
        "set_parent()": 2,
    }

    assert set(counted) == set(EXPECTED), (
        "a query was added, renamed or moved and this table was not updated: "
        f"{sorted(set(counted) ^ set(EXPECTED))}"
    )

    unscoped = sorted(name for name, n in counted.items() if n == 0)
    assert not unscoped, (
        f"these carry no owner predicate at all: {unscoped}. Every read and every write in this "
        "repository is scoped to one owner, and one that is not cannot be reached safely from a "
        "route"
    )

    thinned = {
        name: (counted[name], want) for name, want in EXPECTED.items() if counted[name] < want
    }
    assert not thinned, (
        f"these lost an owner predicate (found, expected): {thinned}. In a recursive CTE the "
        "recursive term needs its own -- without it the walk leaves the owner's subtree and the "
        "anchor's predicate does not stop it"
    )


@pytest.mark.invariant
def test_the_losing_registration_is_202_not_500(
    anon: FlaskClient,
    registered: Callable[..., int],
    session: OrmSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other side of the race: both callers pass the existence check.

    `test_two_registrations_of_one_address_race_to_the_same_answer` takes the
    path where the second request *finds* the row, which is what happens when
    the two are far enough apart. This takes the path where they are not: both
    probe, both see nothing, both insert, and the unique index decides.

    Simulated by making the probe look at an id that cannot exist, which
    produces exactly the interleaving without needing two connections and a
    way to suspend one mid-request. The point is what the loser gets back:
    **202, the same as the winner**. An unhandled violation here is a 500, and
    a 500 on registration is an oracle -- it means somebody else has this
    address.
    """
    from sqlalchemy import select as real_select

    from file_explorer.models.user import User

    registered("contested@example.com")
    session.commit()

    monkeypatch.setattr(
        "file_explorer.blueprints.auth.select",
        lambda *_args, **_kwargs: real_select(User.id).where(User.id == -1),
    )

    response = anon.post(
        "/api/v1/auth/register", json={"email": "contested@example.com", "password": PASSWORD}
    )
    assert response.status_code == 202, response.get_data(as_text=True)
    assert response.get_json() == {"accepted": True}

    count = session.execute(
        text("SELECT count(*) FROM users WHERE email = 'contested@example.com'")
    ).scalar_one()
    assert count == 1, "the race created a second account"


# --- what Gate 3 found ----------------------------------------------------


@pytest.mark.invariant
def test_a_failed_authentication_is_still_counted(app: Flask) -> None:
    """Gate 3's G3-01: a rejection that costs the caller nothing is not a limit.

    `authenticate` used to raise, and it is registered before the limiter so
    that the key is settled before the limiter asks. Flask stops walking
    `before_request` at the first exception -- so the limiter's own hook never
    ran for a request that failed authentication, and anything presenting a bad
    cookie could be sent indefinitely at no cost.

    Resolve, count, enforce: three hooks, in that order.
    """
    from file_explorer.extensions import limiter

    limit = int(app.config["RATELIMIT_DEFAULT"].split()[0])
    anonymous = app.test_client()

    limiter.reset()
    try:
        statuses = [anonymous.get("/api/v1/roots").status_code for _ in range(limit + 5)]
    finally:
        limiter.reset()

    assert 401 in statuses, "the unauthenticated requests were not rejected at all"
    assert 429 in statuses, (
        "a request that fails authentication is never counted, so an attacker can send them "
        "indefinitely at no cost"
    )


@pytest.mark.invariant
def test_a_non_ascii_csrf_token_is_403_not_500(client: FlaskClient) -> None:
    """Gate 3's G3-04.

    Werkzeug decodes WSGI header values as latin-1, so a byte above 0x7F
    arrives as a `str` with non-ASCII code points -- and `hmac.compare_digest`
    raises `TypeError` on exactly that, which escapes to the last-resort
    handler. The caller gets 500 where 403 was meant: a cheaper request to
    send and a noisier one to receive.
    """
    response = client.post(
        "/api/v1/nodes",
        json={"parent_id": None, "name": "x", "type": "folder"},
        headers={CSRF_HEADER: "tokén-with-an-accent"},
    )
    assert response.status_code == 403, response.get_data(as_text=True)


@pytest.mark.invariant
def test_a_proxied_deployment_must_declare_its_hop_count() -> None:
    """Gate 3's BLOCK, as a startup check.

    Behind a proxy with `TRUST_PROXY_HOPS = 0`, `remote_addr` is the proxy for
    every caller on earth -- so the credential routes' ten-per-minute limit is
    ten per minute *in total*, and one script locks everybody out of sign-in at
    negligible cost. D-22's rewrite puts a proxy in front of this API
    unconditionally, so that is the default shape.

    Nothing at the HTTP level sees it: under the test client `remote_addr` is
    the real caller, so every rate-limit test passes while describing a world
    the deployment is not in.
    """
    from file_explorer.app import create_app
    from file_explorer.config import Config

    class Proxied(Config):
        BEHIND_PROXY = True
        TRUST_PROXY_HOPS = 0

    with pytest.raises(RuntimeError, match="TRUST_PROXY_HOPS"):
        create_app(Proxied)

    class Declared(Proxied):
        TRUST_PROXY_HOPS = 1

    create_app(Declared)


@pytest.mark.invariant
def test_samesite_none_without_secure_is_refused_at_startup() -> None:
    """Gate 3's G3-05. Every current browser refuses to store that cookie.

    The easier failure to reach than the cross-site one, because turning
    `Secure` off is what somebody does to make a thing work over plain HTTP.
    """
    from file_explorer.app import create_app
    from file_explorer.config import Config

    class Insecure(Config):
        AUTH_COOKIE_SAMESITE = "None"
        AUTH_COOKIE_SECURE = False

    with pytest.raises(RuntimeError, match="AUTH_COOKIE_SECURE"):
        create_app(Insecure)


@pytest.mark.invariant
def test_registration_does_not_report_success_for_a_constraint_violation(
    anon: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate 3's G3-03: 202 on *any* integrity error reports an account that does not exist.

    `IntegrityError` wraps every integrity condition, not just a unique
    violation, and `users` carries three check constraints. Only 23505 means
    "somebody else got there first"; everything else has to surface.
    """
    monkeypatch.setattr(
        "file_explorer.blueprints.auth.passwords.hash_password",
        lambda _password: "not an argon2id hash",
    )

    response = anon.post(
        "/api/v1/auth/register", json={"email": "bad-hash@example.com", "password": PASSWORD}
    )
    assert response.status_code == 500, (
        "a check-constraint violation was reported as 202, so the caller believes an account "
        "exists that does not"
    )
