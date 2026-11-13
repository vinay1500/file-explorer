"""Application factory.

A factory rather than a module-level app so tests can build an instance with
their own configuration, and so importing the package never has the side effect
of connecting to a database.
"""

from __future__ import annotations

from flask import Flask
from sqlalchemy import text

from file_explorer.config import Config
from file_explorer.extensions import db, migrate


def create_app(config_object: type[Config] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object or Config)

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
