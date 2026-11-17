"""The health check, the last-resort error handler, and the app wiring.

Coverage found all of these. Not because the number was low -- it was 96% --
but because the uncovered 4% was the interesting 4%: the health endpoint that
replaced the one which lied for seventeen months, the handler that decides
whether an unanticipated bug leaks a stack trace, and the branch that decides
whether the rate limiter trusts a forwarded address.

That is the argument for measuring coverage rather than trusting a total. A
well-covered file can still have nothing behind the five lines that matter.
"""

from __future__ import annotations

import logging

import pytest
from flask import Flask, request
from flask.testing import FlaskClient
from sqlalchemy.exc import OperationalError

from file_explorer import auth
from file_explorer.app import create_app
from file_explorer.config import Config
from file_explorer.extensions import db, limiter


@pytest.fixture
def wired(app: Flask) -> Flask:
    """The session app.

    It used to configure an actor. There is no configurable actor any more --
    `DEV_ACTOR_ID` was deleted with the change that made `require_actor()` read
    a session -- so the fixture is the app, and the tests that need to be
    signed in say so by asking for `client`.
    """
    return app


# ----------------------------------------------------------- the health check


@pytest.mark.invariant
def test_health_executes_a_query_rather_than_returning_a_constant(wired: Flask) -> None:
    """F-24, and the whole of chapter 7.

    The original route returned a constant string. It could not fail while the
    process was alive, so every uptime signal stayed green for seventeen months
    while the database was gone.

    This one executes a query. The test that matters is the *next* one -- that
    it goes red when the database does -- because a health check that only ever
    answers 200 is indistinguishable from the one it replaced.
    """
    response = wired.test_client().get("/api/v1/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "db": "ok"}


@pytest.mark.invariant
def test_health_reports_degraded_when_the_database_is_unreachable(
    wired: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    """The half that proves the other half means something.

    Without this, `test_health_executes_a_query...` is satisfied by
    `return {"status": "ok"}`, which is the bug it exists to prevent.
    """

    def unreachable(*_: object, **__: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("could not translate host name"))

    # Not `monkeypatch.setattr`. `db.session` is a scoped_session *proxy*, and
    # `execute` reaches the real Session through `__getattr__` rather than
    # living on the proxy. monkeypatch records the proxied value and restores
    # it by assignment, which leaves a permanent instance attribute shadowing
    # the proxy for every later test in the session.
    db.session.execute = unreachable  # type: ignore[method-assign]
    try:
        with caplog.at_level(logging.ERROR):
            response = wired.test_client().get("/api/v1/health")
    finally:
        del db.session.execute  # type: ignore[attr-defined]

    assert "execute" not in vars(db.session), "the patched method outlived the test"
    assert response.status_code == 503
    assert response.get_json() == {"status": "degraded", "db": "unreachable"}

    # The reason is logged and never returned: it names the database host.
    assert "could not translate host name" not in response.data.decode()
    assert any("health check failed" in r.getMessage() for r in caplog.records), (
        "the failure was answered but never logged, so nothing would notice"
    )


# ------------------------------------------------------- the last resort


@pytest.mark.invariant
def test_an_unexpected_error_inside_a_route_does_not_leak(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `Exception` handler, which had never run.

    Chapter 11 calls this "the one that decides whether an unanticipated bug
    leaks a stack trace", and coverage showed nothing had ever reached it. The
    `after_request` test exercises Flask's `handle_exception` path instead,
    which resolves to the `HTTPException` handler.
    """
    built = create_app(Config)

    # Public for the duration, rather than given a session: this test is about
    # what the last-resort handler does with an exception, not about who was
    # allowed to cause one. Through `monkeypatch` so the allowlist is restored
    # -- a module-level set that one test widens is a set that stays wide.
    monkeypatch.setattr(auth, "PUBLIC_RULES", auth.PUBLIC_RULES | {"/api/v1/boom"})

    @built.get("/api/v1/boom")
    def _boom() -> str:
        raise RuntimeError("connection to postgres://user:hunter2@db:5432 failed")

    with caplog.at_level(logging.ERROR):
        response = built.test_client().get("/api/v1/boom")

    assert response.status_code == 500
    assert response.content_type.startswith("application/problem+json")
    body = response.get_json()
    assert body["code"] == "INTERNAL_ERROR"

    rendered = response.data.decode()
    assert "hunter2" not in rendered, "the exception message leaked into the response"
    assert "Traceback" not in rendered
    assert "RuntimeError" not in rendered
    assert any("unhandled error" in r.getMessage() for r in caplog.records), (
        "the error was swallowed without a log line, which is worse than leaking"
    )


# ------------------------------------------------------------- the wiring


@pytest.mark.invariant
def test_a_forwarded_address_is_trusted_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`TRUST_PROXY_HOPS` is the difference between two opposite failures.

    Zero and behind a balancer: every caller shares the balancer's address, so
    the rate limit becomes a global outage switch. Non-zero and *not* behind
    one: every caller sets their own key through `X-Forwarded-For` and the
    limit stops existing. Neither is safe to guess, so it is explicit.

    Asserted on what the app actually sees, not on a class name. The first
    version checked `type(app.wsgi_app).__name__ == "ProxyFix"`, which
    `ProxyFix(..., x_for=0)` satisfies while trusting nothing.
    """

    class Trusting(Config):
        TRUST_PROXY_HOPS = 1

    seen: dict[str, str | None] = {}

    # Public for the duration: `before_request` would 401 this ad-hoc route
    # before the view could record anything, and what is under test is which
    # address ProxyFix leaves behind, not who is allowed to ask.
    monkeypatch.setattr(auth, "PUBLIC_RULES", auth.PUBLIC_RULES | {"/api/v1/whoami"})

    def record(built: Flask) -> str | None:
        @built.get("/api/v1/whoami")
        def _whoami() -> str:
            seen["addr"] = request.remote_addr
            return "ok"

        built.test_client().get("/api/v1/whoami", headers={"X-Forwarded-For": "203.0.113.9"})
        return seen.get("addr")

    ignored = record(create_app(Config))
    trusted = record(create_app(Trusting))

    assert ignored != "203.0.113.9", (
        "a forwarded address was trusted with TRUST_PROXY_HOPS unset, so any "
        "caller can choose their own rate-limit key"
    )
    assert trusted == "203.0.113.9", (
        "TRUST_PROXY_HOPS is set and the forwarded address is still ignored, so "
        "behind a balancer every caller shares one bucket"
    )


@pytest.mark.invariant
def test_the_error_response_component_is_retyped_or_says_why(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The retype is silent on success and loud on failure, by design.

    A document that disagrees with the service about the media type is exactly
    the defect this project shipped once already, and it is invisible unless
    something says so.
    """
    with caplog.at_level(logging.WARNING):
        built = create_app(Config)

    spec = built.extensions["flask-smorest"]["apis"][""]["ext_obj"].spec.to_dict()
    default = spec["components"]["responses"]["DEFAULT_ERROR"]
    assert sorted(default["content"]) == ["application/problem+json"]

    warnings = [r.getMessage() for r in caplog.records if "retype" in r.getMessage()]
    assert not warnings, f"the retype reported a problem: {warnings}"


@pytest.mark.invariant
def test_a_marshmallow_error_raised_outside_a_route_is_still_a_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `ValidationError` handler, which no HTTP request reaches.

    flask-smorest catches marshmallow inside `@blp.arguments` and re-raises it
    as an `HTTPException`, so the dedicated handler never runs through a route.
    It is still the right handler for a `ValidationError` raised anywhere else
    — and an untested handler is one that is wrong the first time it matters.
    """
    from marshmallow import ValidationError

    built = create_app(Config)

    # Public for the duration. Authentication runs in `before_request` now, so
    # an ad-hoc route on a fresh app is a 401 before the view raises anything
    # -- which is the point of moving it there, and is why this test went red
    # on a change that had nothing to do with validation.
    monkeypatch.setattr(auth, "PUBLIC_RULES", auth.PUBLIC_RULES | {"/api/v1/validate"})

    @built.get("/api/v1/validate")
    def _validate() -> str:
        raise ValidationError({"name": ["Not a valid string."]})

    response = built.test_client().get("/api/v1/validate")

    assert response.status_code == 422
    assert response.content_type.startswith("application/problem+json")
    body = response.get_json()
    assert body["code"] == "VALIDATION_FAILED"
    assert body["errors"] == {"name": ["Not a valid string."]}


@pytest.mark.invariant
def test_a_repr_does_not_carry_user_content(session: object, alice: int) -> None:
    """`__repr__` lands in logs and tracebacks, so it must not carry content.

    Both models say so in their own comments. The first version of this test
    asserted only that the string mentioned the class — adding `name=` and
    `email=` would have survived it, which is the property the comments are
    actually about.
    """
    from file_explorer.models.node import Node, NodeType
    from file_explorer.models.user import User

    # NodeType, not the string: `__repr__` reads `.value`.
    node = Node(owner_id=alice, name="quarterly-layoffs.xlsx", type=NodeType.FOLDER)
    user = session.get(User, alice)  # type: ignore[attr-defined]

    rendered = repr(node)
    assert "Node" in rendered
    assert "folder" in rendered
    assert "quarterly-layoffs" not in rendered, (
        "the node name is in its repr, so every log line carrying one leaks a "
        "filename the owner chose"
    )

    rendered = repr(user)
    assert f"id={alice}" in rendered
    assert "@" not in rendered, "the email address is in the user repr"


@pytest.mark.invariant
def test_a_rate_limited_response_keeps_retry_after_and_the_rest_do_not(
    wired: Flask, client: FlaskClient
) -> None:
    """Both arms of the header strip, driven against the real configured limit.

    Two earlier attempts at this were wrong in instructive ways.

    The first sent twelve requests against a limit of a hundred and twenty, so
    deleting the exemption it was testing survived it.

    The second built an app with `RATELIMIT_DEFAULT = "2 per minute"` and got
    `X-RateLimit-Limit: 120` back. **`Limiter` is a module-level singleton and
    takes its limits from whichever app initialises it first.** With one
    application that is invisible; here it meant the configuration under test
    was never in force. The diagnostic that found it is worth more than the
    test: `enabled=True default='2 per minute' … headers={'X-RateLimit-Limit':
    '120'}`.

    So this drives the limit the application actually has, and resets the
    shared bucket on both sides — otherwise it spends the budget every later
    test in the same minute is sharing.
    """
    limit = int(wired.config["RATELIMIT_DEFAULT"].split()[0])
    limiter.reset()
    try:
        responses = [client.get("/api/v1/roots") for _ in range(limit + 5)]
    finally:
        limiter.reset()

    limited = [r for r in responses if r.status_code == 429]
    allowed = [r for r in responses if r.status_code != 429]

    assert limited, (
        f"the limiter never fired in {limit + 5} requests against a limit of "
        f"{limit}; X-RateLimit-Limit was "
        f"{responses[0].headers.get('X-RateLimit-Limit')}"
    )
    assert limited[0].content_type.startswith("application/problem+json")
    assert limited[0].get_json()["code"] == "RATE_LIMITED"
    assert limited[0].headers.get("Retry-After"), "a 429 with no Retry-After says nothing"
    # The same `after_request` sets these, so their absence would mean a 429
    # bypasses it and carries neither `no-store` nor `nosniff`.
    assert limited[0].headers.get("Cache-Control") == "no-store"
    assert limited[0].headers.get("X-Content-Type-Options") == "nosniff"

    assert allowed, "every request was limited; the successful arm is untested"
    assert allowed[0].headers.get("Retry-After") is None, (
        "Retry-After on a successful response means nothing and may be honoured"
    )


@pytest.mark.invariant
def test_health_is_exempt_from_the_limiter(wired: Flask) -> None:
    """The exemption, driven against health's **own** bucket.

    Flask-Limiter applies its default limits per endpoint, so each route has a
    separate bucket per caller. The earlier version of this spent `/roots`'s
    budget and then asked `/health` — which passes with or without the
    exemption, because it was never the same bucket. Removing
    `@limiter.exempt` survived it.

    The limit is therefore **per endpoint per address**, not a global ceiling,
    and the configuration comment now says so.

    A limiter that can silence the health check turns a busy minute into an
    outage: the one endpoint that reports whether the service is alive stops
    answering precisely when somebody is asking.
    """
    limit = int(wired.config["RATELIMIT_DEFAULT"].split()[0])
    client = wired.test_client()

    limiter.reset()
    try:
        statuses = {client.get("/api/v1/health").status_code for _ in range(limit + 10)}
    finally:
        limiter.reset()

    assert 429 not in statuses, (
        f"health was rate limited after {limit + 10} requests, so a busy minute "
        "silences the one endpoint that reports whether the service is alive"
    )
    assert statuses <= {200, 503}, f"unexpected statuses from health: {statuses}"


# --- No DDL over HTTP, ever (F-04) ----------------------------------------

# Every rule the application is allowed to register. An allowlist rather than a
# pattern, because the route this guards against was called `/create-tables`
# and no plausible pattern would have objected to that name.
ALLOWED_RULES = frozenset(
    {
        "/static/<path:filename>",
        "/api/openapi.json",
        "/api/v1/health",
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/guest",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/roots",
        "/api/v1/nodes",
        "/api/v1/nodes/<int:node_id>",
        "/api/v1/nodes/<int:node_id>/children",
        "/api/v1/nodes/<int:node_id>/path",
        "/api/v1/nodes/<int:node_id>/move",
    }
)


@pytest.mark.invariant
def test_no_ddl_routes_registered(app: Flask) -> None:
    """The deployed service exposed `GET /create-tables`, which ran `create_all()`.

    That is the worst defect in the original and the reason this file exists.
    An allowlist is the right shape here: the failure was not a route with a
    suspicious name -- `/create-tables` is a perfectly ordinary name -- it was a
    route nobody was counting. Anything new has to be added here deliberately,
    and the diff that adds it is the review.
    """
    registered = {rule.rule for rule in app.url_map.iter_rules()}
    unexpected = registered - ALLOWED_RULES
    assert not unexpected, f"routes nobody accounted for: {sorted(unexpected)}"

    # And the other direction, so the allowlist cannot quietly outlive the
    # routes it describes and go on passing against an empty url map.
    missing = ALLOWED_RULES - registered - {"/static/<path:filename>"}
    assert not missing, f"the allowlist names routes that no longer exist: {sorted(missing)}"


@pytest.mark.invariant
def test_the_source_contains_no_schema_creation_calls() -> None:
    """`create_all` must not come back, by any route or none.

    The route test above catches an endpoint that performs DDL. This catches the
    call itself, reached any other way -- a CLI command, a request hook, a
    well-meant "initialise on first run". Schema changes are Alembic migrations
    run as a deployment step, which is why `migrations/` is deliberately not
    searched.
    """
    import ast
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "src" / "file_explorer"
    assert package.is_dir(), f"{package} is not where this test expects it"

    forbidden = {"create_all", "drop_all"}
    offences: list[str] = []
    seen = 0
    for path in sorted(package.rglob("*.py")):
        seen += 1
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                offences.append(f"{path.name}:{node.lineno} .{node.attr}")
            elif isinstance(node, ast.Name) and node.id in forbidden:
                offences.append(f"{path.name}:{node.lineno} {node.id}")

    assert seen > 10, f"only {seen} modules walked; the glob is wrong and this proves nothing"
    assert not offences, f"schema creation reached the package again: {offences}"
