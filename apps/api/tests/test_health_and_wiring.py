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
from collections.abc import Iterator

import pytest
from flask import Flask, request
from sqlalchemy.exc import OperationalError

from file_explorer.app import create_app
from file_explorer.config import Config
from file_explorer.extensions import db


@pytest.fixture
def wired(app: Flask) -> Iterator[Flask]:
    """The session app with an actor configured, restored afterwards."""
    previous = app.config.get("DEV_ACTOR_ID")
    app.config["DEV_ACTOR_ID"] = 1
    try:
        yield app
    finally:
        app.config["DEV_ACTOR_ID"] = previous


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
    wired: Flask, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The half that proves the other half means something.

    Without this, `test_health_executes_a_query...` is satisfied by
    `return {"status": "ok"}`, which is the bug it exists to prevent.
    """

    def unreachable(*_: object, **__: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("could not translate host name"))

    monkeypatch.setattr(db.session, "execute", unreachable)

    with caplog.at_level(logging.ERROR):
        response = wired.test_client().get("/api/v1/health")

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
) -> None:
    """The `Exception` handler, which had never run.

    Chapter 11 calls this "the one that decides whether an unanticipated bug
    leaks a stack trace", and coverage showed nothing had ever reached it. The
    `after_request` test exercises Flask's `handle_exception` path instead,
    which resolves to the `HTTPException` handler.
    """
    built = create_app(Config)
    built.config["DEV_ACTOR_ID"] = 1

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
def test_a_forwarded_address_is_trusted_only_when_configured() -> None:
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
def test_a_marshmallow_error_raised_outside_a_route_is_still_a_problem() -> None:
    """The `ValidationError` handler, which no HTTP request reaches.

    flask-smorest catches marshmallow inside `@blp.arguments` and re-raises it
    as an `HTTPException`, so the dedicated handler never runs through a route.
    It is still the right handler for a `ValidationError` raised anywhere else
    — and an untested handler is one that is wrong the first time it matters.
    """
    from marshmallow import ValidationError

    built = create_app(Config)

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
