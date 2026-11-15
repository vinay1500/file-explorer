"""Settings, read from the environment.

Nothing here has a usable default for production. A missing DATABASE_URL is a
startup failure, not a silent fallback to something unexpected.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, ClassVar

from file_explorer import limits


class Config:
    SQLALCHEMY_DATABASE_URI = os.environ["DATABASE_URL"]
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Bounds, because nothing here had any. A runaway recursive query on
    # cyclic data would otherwise hold a connection until the process died --
    # and gunicorn's worker timeout kills the worker, not the query.
    STATEMENT_TIMEOUT_MS = int(os.environ.get("STATEMENT_TIMEOUT_MS", limits.STATEMENT_TIMEOUT_MS))
    LOCK_TIMEOUT_MS = int(os.environ.get("LOCK_TIMEOUT_MS", limits.LOCK_TIMEOUT_MS))
    IDLE_IN_TRANSACTION_TIMEOUT_MS = int(
        os.environ.get("IDLE_IN_TRANSACTION_TIMEOUT_MS", limits.IDLE_IN_TRANSACTION_TIMEOUT_MS)
    )

    POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "5"))
    MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "5"))

    # Debug is off unless explicitly asked for. The original code had
    # debug=True inside `if __name__ == "__main__"` -- harmless under gunicorn,
    # which imports rather than executes, but the wrong default to carry.
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

    # I7, and the bound that keeps the cycle check sound. See limits.py.
    MAX_TREE_DEPTH = int(os.environ.get("MAX_TREE_DEPTH", limits.MAX_TREE_DEPTH))
    MAX_NODES_PER_USER = int(os.environ.get("MAX_NODES_PER_USER", limits.MAX_NODES_PER_USER))

    # Exact origins, no wildcard. An empty value rejects every cross-origin
    # request, which is the correct default (F-05).
    CORS_ALLOWED_ORIGINS: ClassVar[list[str]] = [
        o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
    ]


def engine_options(config: Mapping[str, Any]) -> dict[str, object]:
    """Build the engine options from whatever configuration is in force.

    A function rather than a class attribute, and that is the whole point. As a
    class attribute the string is built once when this module is imported, so a
    subclass overriding `STATEMENT_TIMEOUT_MS` -- which is exactly what a test
    wants to do -- would change the number and not the connection. The config
    would look wired and would not be, which is the bug this task existed to
    fix, one layer further up.
    """
    return {
        "pool_size": config["POOL_SIZE"],
        "max_overflow": config["MAX_OVERFLOW"],
        "pool_pre_ping": True,
        "connect_args": {
            "options": (
                f"-c statement_timeout={os.environ.get('STATEMENT_TIMEOUT_MS', '5000')} "
                f"-c lock_timeout={os.environ.get('LOCK_TIMEOUT_MS', '3000')} "
                "-c idle_in_transaction_session_timeout="
                f"{os.environ.get('IDLE_IN_TRANSACTION_TIMEOUT_MS', '10000')}"
            )
        },
    }
