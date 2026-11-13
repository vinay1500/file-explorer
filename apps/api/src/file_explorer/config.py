"""Settings, read from the environment.

Nothing here has a usable default for production. A missing DATABASE_URL is a
startup failure, not a silent fallback to something unexpected.
"""

from __future__ import annotations

import os
from typing import ClassVar


class Config:
    SQLALCHEMY_DATABASE_URI = os.environ["DATABASE_URL"]
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Debug is off unless explicitly asked for. The original code had
    # debug=True inside `if __name__ == "__main__"` -- harmless under gunicorn,
    # which imports rather than executes, but the wrong default to carry.
    DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

    # Invariant I7. A tree with no depth limit is a denial-of-service vector:
    # a script can create 100,000 nested folders through the normal API and
    # any recursive query over that chain then exhausts the connection.
    MAX_TREE_DEPTH = int(os.environ.get("MAX_TREE_DEPTH", "64"))
    MAX_NODES_PER_USER = int(os.environ.get("MAX_NODES_PER_USER", "50000"))

    # Exact origins, no wildcard. An empty value rejects every cross-origin
    # request, which is the correct default (F-05).
    CORS_ALLOWED_ORIGINS: ClassVar[list[str]] = [
        o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
    ]
