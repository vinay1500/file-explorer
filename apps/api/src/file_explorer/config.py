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

    # --- HTTP layer -------------------------------------------------------

    API_TITLE = "File Explorer API"
    API_VERSION = "v1"
    OPENAPI_VERSION = "3.0.3"
    OPENAPI_URL_PREFIX = "/api"
    OPENAPI_JSON_PATH = "openapi.json"

    # PROPAGATE_EXCEPTIONS is deliberately NOT set.
    #
    # It was, with a comment claiming it made smorest return the handler's
    # value. That is not what it does. Its only effect is in
    # `Flask.handle_exception`: set to True, Flask re-raises to the WSGI server
    # anything that escapes dispatch -- an `after_request` function, response
    # processing, a failing handler -- instead of running it through the
    # registered 500 handler. Under gunicorn that is a bare HTML page and a
    # traceback, which is exactly what `problems.py` exists to make impossible.
    #
    # The `Exception` handler already covers everything raised inside dispatch,
    # so the setting bought nothing and cost the last-resort guarantee.

    # --- session cookie ---------------------------------------------------
    #
    # `DEV_ACTOR_ID` used to live here. It is deleted rather than defaulted,
    # and deleted in the same change that taught `require_actor()` to read a
    # session, because any version that keeps both sources fails open: config
    # first serves every request as one user, config as a fallback serves every
    # unauthenticated one. See `auth.py`.

    # Off only where there is no HTTPS, which is the Compose stack. Anywhere
    # real this is 1, and `0` in a deployed environment is the mistake this
    # setting exists to make visible rather than silent.
    AUTH_COOKIE_SECURE = os.environ.get("AUTH_COOKIE_SECURE", "1") == "1"

    # `Lax` is correct **because D-22 makes the browser same-site**: the
    # frontend reaches the API through a rewrite on its own origin, so the
    # cookie is never sent cross-site and never needs `None`. If the two halves
    # are ever split across registrable domains again, `Lax` stops being sent
    # at all and the demo silently cannot sign in -- which is the Gate 1 BLOCK
    # that produced D-22.
    AUTH_COOKIE_SAMESITE = os.environ.get("AUTH_COOKIE_SAMESITE", "Lax")

    # A name is at most 255 characters, so a request body has no business being
    # large. Werkzeug raises 413 above this, before the body is read into
    # memory -- which is the point: without it, the limit is however much RAM
    # the worker has.
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", "16384"))

    # Two things this is not, both worth saying because the number looks like
    # a global ceiling and is not.
    #
    # It is **per endpoint**: Flask-Limiter applies default limits per route,
    # so this is 120/minute to `/roots` *and* 120/minute to `/nodes`, per
    # caller. Found by a mutation -- removing the health exemption survived a
    # test that spent one route's budget and then asked another.
    #
    # It is **per process**: the storage is in-memory, so with more than one
    # worker the effective limit is this times the worker count, and a restart
    # clears it. Honest for a single free-tier instance and wrong anywhere
    # else; RATELIMIT_STORAGE_URI points at Redis when there is one.
    # How many proxies sit in front of this app. 0 means none, and
    # `remote_addr` is the caller.
    #
    # It matters because the rate limiter keys on `remote_addr`. Behind a load
    # balancer without ProxyFix that is the *balancer's* address, so every
    # caller in the world shares one bucket and the limit becomes a global
    # outage switch. With ProxyFix and no balancer, the caller sets their own
    # key through `X-Forwarded-For` and the limit stops existing.
    #
    # There is no safe default, so this is explicit and defaults to 0. Set it
    # to the real number of hops; do not guess high.
    TRUST_PROXY_HOPS = int(os.environ.get("TRUST_PROXY_HOPS", "0"))

    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_DEFAULT = os.environ.get("RATELIMIT_DEFAULT", "120 per minute")
    RATELIMIT_HEADERS_ENABLED = True

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
                f"-c statement_timeout={config['STATEMENT_TIMEOUT_MS']} "
                f"-c lock_timeout={config['LOCK_TIMEOUT_MS']} "
                "-c idle_in_transaction_session_timeout="
                f"{config['IDLE_IN_TRANSACTION_TIMEOUT_MS']}"
            )
        },
    }
