"""Application factory.

A factory rather than a module-level app so tests can build an instance with
their own configuration, and so importing the package never has the side effect
of connecting to a database.
"""

from __future__ import annotations

import logging

from flask import Flask, current_app
from flask.wrappers import Response
from flask_smorest import Api
from sqlalchemy import text
from sqlalchemy.orm import Session
from werkzeug.middleware.proxy_fix import ProxyFix

from file_explorer import auth as auth_module, problems
from file_explorer.config import Config, engine_options
from file_explorer.extensions import db, limiter, migrate
from file_explorer.problems import PROBLEM_CONTENT_TYPE
from file_explorer.schemas.node import ProblemSchema
from file_explorer.services.tree import TreeService

log = logging.getLogger(__name__)


class ProblemApi(Api):
    """`Api`, with the error shape the handlers actually send.

    smorest documents its own `Error` schema on every operation --
    `{code: integer, message: string, errors: object}` -- and that is not what
    `problems.py` returns. Left alone, the generated document promises one
    shape and the service sends another, so a generated client's error type is
    wrong everywhere and nobody finds out until something fails in production.

    `ERROR_SCHEMA` is the single supported override for it.
    """

    ERROR_SCHEMA = ProblemSchema


def _retype_default_error(api: Api) -> None:
    """Re-declare the shared error response as `application/problem+json`.

    `ERROR_SCHEMA` sets the *schema* of smorest's `DEFAULT_ERROR` component and
    leaves its media type at `Api.DEFAULT_RESPONSE_CONTENT_TYPE`, which is
    `application/json`. There is no class attribute for the pair, and setting
    the default content type on the Api would retype successful responses too.

    So the component is rewritten after registration. Done here rather than in
    the blueprint because it is one shared component; the per-operation
    responses pass `content_type` explicitly.
    """
    responses = getattr(api.spec.components, "responses", None)
    if not isinstance(responses, dict):
        log.warning("cannot retype the error response: components.responses is %r", responses)
        return

    retyped = 0
    for name, response in responses.items():
        content = response.get("content") if isinstance(response, dict) else None
        if isinstance(content, dict) and "application/json" in content:
            content[PROBLEM_CONTENT_TYPE] = content.pop("application/json")
            retyped += 1
            log.debug("retyped response component %s", name)

    if not retyped:
        # Loud rather than silent: a document that disagrees with the service
        # about the media type is the defect this function exists to prevent,
        # and it is invisible unless something says so.
        log.warning(
            "no error response component was retyped; components.responses has %s",
            sorted(responses),
        )


def create_app(config_object: type[Config] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object or Config)

    # Built here, from the configuration actually in force, so a subclass that
    # overrides a timeout gets a connection that honours it. See
    # `config.engine_options` for why this is not a class attribute.
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = engine_options(app.config)

    db.init_app(app)
    migrate.init_app(app, db)

    # Imported for their side effect of registering tables on the metadata.
    # Alembic autogenerate silently misses a table whose module was never
    # imported, which produces an empty migration and a confusing failure later.
    from file_explorer import models  # noqa: F401

    @app.after_request
    def _response_headers(response: Response) -> Response:
        """Headers every response gets, whatever produced it.

        `no-store` because every body is scoped to one owner, and identity
        becomes a cookie at M5 -- which a CDN does not key on. A single cached
        200 would serve one user's tree to the next.

        `nosniff` because responses are JSON and problem+JSON, and a browser
        that guesses otherwise about a body containing user-chosen names is a
        browser deciding to execute them.
        """
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Flask-Limiter adds Retry-After to every response once rate-limit
        # headers are on, including successful ones, where it means nothing and
        # a cautious client may well honour it.
        #
        # **Registered before `limiter.init_app`, deliberately.** Flask runs
        # `after_request` functions in reverse registration order, so the one
        # registered first runs last. Registered after the limiter, this ran
        # before it and the header was added back immediately afterwards --
        # the strip was dead code, and only a test that drove a real 429
        # noticed.
        if response.status_code != 429:
            response.headers.pop("Retry-After", None)
        return response

    hops = app.config.get("TRUST_PROXY_HOPS", 0)
    if hops:
        # Only when told. Trusting X-Forwarded-For with nothing in front means
        # every caller chooses their own rate-limit key.
        app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops
        )

    limiter.init_app(app)

    # `ERROR_SCHEMA` registers the Problem component itself, so registering it
    # again here raises DuplicateComponentNameError.
    api = ProblemApi(app)

    from file_explorer.blueprints.auth import blp as auth_blp
    from file_explorer.blueprints.nodes import blp as nodes_blp

    api.register_blueprint(auth_blp)
    api.register_blueprint(nodes_blp)

    # **After the blueprints.** `before_request` functions run in registration
    # order, and this one resolves the session every route depends on, so it
    # would be equally correct earlier -- but registering it here keeps the
    # reading order honest: routes exist, then the thing that guards them.
    auth_module.register(app)

    # After the blueprint: smorest registers its shared error response while
    # wiring routes, so doing this at Api() time finds nothing.
    _retype_default_error(api)

    # **After** `Api(app)`, deliberately. flask-smorest installs its own
    # HTTPException handler during `init_app`, and Flask keeps handlers in a
    # dict keyed by exception class -- so the later registration **replaces**
    # the earlier one. There is no ordering and no fallthrough. Register these
    # first and smorest's wins, which means 404, 405 and 413 come back in its
    # shape while the domain errors are RFC 9457: a client has to handle both,
    # which is the exact failure this module exists to prevent.
    problems.register(app)

    @app.get("/api/v1/health")
    @limiter.exempt
    def health() -> tuple[dict[str, str], int]:
        """Readiness, not liveness.

        The original service had a route that returned a constant string. It
        could not fail while the process was alive, so every uptime signal
        stayed green for seventeen months while the database was gone (F-24).
        This one executes a query, because a health check that does not
        exercise its dependency is decoration.
        """
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            # The reason is logged, never returned -- it would leak the
            # database host and driver details to any caller (SECURITY §3).
            app.logger.exception("health check failed")
            return {"status": "degraded", "db": "unreachable"}, 503
        return {"status": "ok", "db": "ok"}, 200

    return app


def tree_service(session: Session | None = None) -> TreeService:
    """Build a `TreeService` from the running application's configuration.

    This is the wiring Gate 1 recorded as missing: `MAX_TREE_DEPTH` and
    `MAX_NODES_PER_USER` were read from the environment into `Config` and then
    read by nothing, so the service ran on whatever its callers hardcoded.

    It lives here rather than in the service module on purpose. `TreeService`
    knows nothing about Flask, and that is worth keeping -- the place that
    knows about both is the application factory's module, not the domain.
    """
    # `db.session` is a scoped_session proxy, not a Session. Calling it
    # resolves the Session registered for this scope -- the one the proxy would
    # have forwarded to -- with a type that says so.
    #
    # That resolution is eager, so the result is **request-scoped**: the
    # TreeService returned here holds that Session object, not the proxy, and
    # keeping one past the end of the request would keep a stale Session. Build
    # one per request; do not cache it on the app.
    return TreeService(
        session if session is not None else db.session(),
        current_app.config["MAX_TREE_DEPTH"],
        current_app.config["MAX_NODES_PER_USER"],
    )
