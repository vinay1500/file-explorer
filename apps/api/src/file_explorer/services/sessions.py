"""Creating, resolving and revoking sessions.

The table is the design (D-21); this is the four operations that touch it.

Every one of them takes the **cookie value** and hashes it here. Nothing outside
this module should ever see a `token_hash`, and nothing inside it should ever
store a token.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import scoped_session

from file_explorer.models.session import (
    Session,
    new_csrf_token,
    new_token,
    token_digest,
)

# How long a session lasts from creation, regardless of use.
DEFAULT_LIFETIME = timedelta(days=14)

# How long a session survives without being used.
IDLE_LIFETIME = timedelta(hours=12)

# `last_seen_at` is only rewritten when it is this stale. Without the floor,
# every authenticated GET becomes a write: a read-only page view would take a
# row lock, and two tabs scrolling would contend on it. With it, the idle
# timeout is accurate to a minute, which is all it needs to be.
TOUCH_INTERVAL = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class Issued:
    """What a caller gets when a session begins.

    The token goes in an httpOnly cookie, so no script can read it. The CSRF
    token must be readable by the page -- that is what makes it a synchronizer
    token the page can echo back in a header -- so it is returned in the body
    and never in an httpOnly cookie.
    """

    token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Resolved:
    """A live session, as the request needs it."""

    actor_id: int
    csrf_token: str


class SessionService:
    def __init__(self, db: DbSession | scoped_session[DbSession]) -> None:
        self._db = db

    def begin(self, user_id: int, lifetime: timedelta = DEFAULT_LIFETIME) -> Issued:
        """Open a session. The caller commits."""
        token = new_token()
        csrf = new_csrf_token()
        now = datetime.now(UTC)
        expires_at = now + lifetime

        self._db.add(
            Session(
                token_hash=token_digest(token),
                user_id=user_id,
                csrf_token=csrf,
                created_at=now,
                last_seen_at=now,
                expires_at=expires_at,
            )
        )
        return Issued(token=token, csrf_token=csrf, expires_at=expires_at)

    def resolve(self, token: str) -> Resolved | None:
        """The live session this token names, or `None`.

        `None` for every reason: no such row, expired by age, expired by
        idleness. The caller cannot tell them apart and does not need to -- each
        one means "sign in again", and distinguishing them in a response would
        say whether a token was ever real.
        """
        if not token:
            return None

        now = datetime.now(UTC)
        row = self._db.execute(
            select(Session).where(Session.token_hash == token_digest(token))
        ).scalar_one_or_none()
        if row is None:
            return None

        # Expiry is enforced here rather than by a `WHERE expires_at > now()`,
        # so an expired row is found and can be deleted on the spot. A query
        # that filters it out leaves it to accumulate until a sweeper runs.
        if row.expires_at <= now or row.last_seen_at + IDLE_LIFETIME <= now:
            self._db.delete(row)
            return None

        if row.last_seen_at + TOUCH_INTERVAL <= now:
            stmt = update(Session).where(Session.token_hash == row.token_hash)
            self._db.execute(stmt.values(last_seen_at=now))

        return Resolved(actor_id=row.user_id, csrf_token=row.csrf_token)

    def revoke(self, token: str) -> int:
        """End one session. Returns how many rows went, which is 0 or 1.

        **This is the sentence a stateless token cannot say.** Clearing a cookie
        asks the browser to forget a credential; it does nothing about a copy
        already taken out of one. Deleting the row ends the session everywhere
        at once.
        """
        if not token:
            return 0
        result = self._db.execute(delete(Session).where(Session.token_hash == token_digest(token)))
        return int(cast("CursorResult[Any]", result).rowcount or 0)

    def revoke_all(self, user_id: int) -> int:
        """End every session for one account.

        What a password change has to do, and what an account recovery has to
        do. It reads `ix_sessions_user_id` rather than the table.
        """
        result = self._db.execute(delete(Session).where(Session.user_id == user_id))
        return int(cast("CursorResult[Any]", result).rowcount or 0)

    def purge_expired(self, now: datetime | None = None) -> int:
        """Delete rows that can no longer authenticate anyone.

        `resolve` already removes an expired row when it meets one, so this is
        for the sessions nobody ever comes back to -- which is most of them.
        Not scheduled yet; it exists so that scheduling it later is a cron entry
        rather than a design.
        """
        at = now or datetime.now(UTC)
        result = self._db.execute(
            delete(Session).where(
                (Session.expires_at <= at) | (Session.last_seen_at + IDLE_LIFETIME <= at)
            )
        )
        return int(cast("CursorResult[Any]", result).rowcount or 0)
