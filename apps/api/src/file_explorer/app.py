"""Application factory.

A factory rather than a module-level app so tests can build an instance with
their own configuration, and so importing the package never has the side effect
of connecting to a database.
"""

from __future__ import annotations

from flask import Flask, current_app
from sqlalchemy import text
from sqlalchemy.orm import Session

from file_explorer.config import Config, engine_options
from file_explorer.extensions import db, migrate
from file_explorer.services.tree import TreeService


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

    @app.get("/api/v1/health")
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
    return TreeService(
        session if session is not None else db.session,
        current_app.config["MAX_TREE_DEPTH"],
        current_app.config["MAX_NODES_PER_USER"],
    )
