"""Shared extension instances.

Kept in their own module so models, the app factory and Alembic can each import
them without importing each other.
"""

from flask import g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Explicit naming so constraint names are deterministic rather than whatever
# the database happens to generate. Without this, Alembic autogenerate produces
# a different name on every machine and migrations stop being reproducible --
# and an unnamed constraint cannot be dropped by name in a later migration.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


db = SQLAlchemy(model_class=Base)
migrate = Migrate()


def rate_limit_key() -> str:
    """Who this request counts against.

    The authenticated actor where there is one, and the address otherwise.

    Keying everything on the address was the M3b placeholder and Gate 1 called
    it out in two directions at once. An address is **shared** -- everyone
    behind one NAT, one campus or one mobile carrier is a single bucket, so a
    busy network makes the limit an outage switch for everybody on it. And an
    address is **chosen** -- behind a proxy that forwards one, a caller who
    wants more than their share sets a different value and gets a fresh bucket.
    Neither is true of a session: it is one person, and they cannot mint
    another without signing in again.

    The auth endpoints still key on the address, because there is no session
    yet when someone is trying to get one. That is the genuinely hard case and
    it is discussed where those limits are declared.

    `g` rather than `request`: `authenticate` has already run by the time the
    limiter asks, so the actor is settled, and reading it here cannot
    accidentally become a second place that decides who the caller is.
    """
    actor = getattr(g, "actor_id", None)
    if actor is not None:
        return f"user:{actor}"
    return f"addr:{get_remote_address()}"


# **This is a singleton, and its limits come from whichever application calls
# `init_app` first.** A second `create_app` with different `RATELIMIT_*`
# settings gets the first one's limits, silently -- found when a test
# configured "2 per minute" and the response said `X-RateLimit-Limit: 120`.
#
# Harmless in production, where there is one application. It matters for tests:
# drive the configured limit rather than trying to lower it, and call
# `limiter.reset()` on both sides, because the in-memory bucket is shared by
# everything else running in the same minute.
limiter = Limiter(key_func=rate_limit_key)
