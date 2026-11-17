"""A signed-in session.

**D-21: an opaque id in a row, not a JWT.** The reasoning is worth keeping next
to the table, because the question comes up immediately.

A JWT buys one thing: verification without touching the database. That is worth
paying for when verification has to happen somewhere the database is not --
across services, at an edge, under load that would not survive the extra read.
This application queries PostgreSQL on every request from a single instance, so
that benefit cannot be collected here, while all of its costs still are: no
revocation, algorithm pinning, clock skew, and a refresh-rotation design. And
the design was already half-stateful -- a refresh token that cannot be revoked
or reuse-detected needs a row anyway -- so the table was arriving regardless and
the JWT would have been a session id with extra steps.

What the row buys back, in one sentence each:

* **Logout is a `DELETE`.** Not "clear the cookie and hope", which is all a
  stateless token can offer: clearing a cookie is a request to the browser, and
  a token already copied out of one keeps working until it expires.
* **Deleting a user ends their sessions**, through `ON DELETE CASCADE`, rather
  than leaving signed tokens that outlive the row they name.
* **Nothing has to be pinned.** No `alg`, no issuer, no leeway, no decision
  about whether a refresh token is acceptable on a resource route.

## The id is stored hashed

The cookie carries 32 random bytes; this table stores their SHA-256. A database
read -- a leaked backup, a log line, a support query -- then yields nothing a
caller can present, which is the same argument as hashing a password.

SHA-256 and not Argon2, deliberately. A password is low-entropy and guessable,
so the hash has to be slow. A 256-bit random token is not guessable at any
speed, so slowness buys nothing and would cost a memory-hard hash on every
single request rather than only at login.

## The CSRF token is stored in the clear, and that is not an oversight

`docs/SECURITY.md` §2.5 specified a double-submit token "compared against the
cookie". Double-submit is defeated by anyone who can write a cookie for the
site -- a sibling subdomain, or a network attacker on a plain-HTTP sibling --
because it only ever compares the request to itself.

Being stateful removes the reason to accept that. The row is already being read
to authenticate, so comparing the request's header against a value stored *in
the row* costs nothing extra and is a **synchronizer token**: bound to the
session, unforgeable by anyone who cannot read this table. It is kept in the
clear because it is not a credential on its own -- without the session cookie it
opens nothing -- and because `/auth/me` can then hand it back to a client that
reloaded and lost it.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from file_explorer.extensions import Base

# 256 bits. `token_urlsafe` returns about 43 characters for this, which is the
# length of the cookie value.
TOKEN_BYTES = 32

# SHA-256 output width, and therefore the primary key's width.
DIGEST_BYTES = 32

# The CSRF token is a separate secret so that handing it to the page -- which is
# the whole point of a synchronizer token -- never reveals anything about the
# session id it is bound to.
CSRF_BYTES = 32
CSRF_MAX_LENGTH = 64


def new_token() -> str:
    """A fresh session id, for the cookie. Never stored."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_digest(token: str) -> bytes:
    """What goes in the table: the SHA-256 of what went in the cookie."""
    return hashlib.sha256(token.encode("utf-8")).digest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(CSRF_BYTES)


class Session(Base):
    __tablename__ = "sessions"

    # The hash is the key. There is no separate id column, because a second
    # identifier would be a second thing to look a session up by, and one of
    # the two would eventually be the one that forgot to check expiry.
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(DIGEST_BYTES), primary_key=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    csrf_token: Mapped[str] = mapped_column(String(CSRF_MAX_LENGTH), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Moved forward on use, so an abandoned session can be expired on idleness
    # as well as on age. Written at most once a minute rather than on every
    # request -- see the service -- because a write on every read turns a
    # read-only page view into a transaction.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # Revoking every session for one user is a single-row-set delete, which
        # is what a password change has to do.
        Index("ix_sessions_user_id", "user_id"),
        # The sweeper deletes by expiry, so it reads an index rather than the
        # table. Also the only index a "how many sessions are live" question
        # needs.
        Index("ix_sessions_expires_at", "expires_at"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint("length(csrf_token) >= 16", name="csrf_long_enough"),
    )

    def __repr__(self) -> str:
        # Never the token hash and never the CSRF token. The hash is not a
        # credential, but printing it into a log makes the log a list of who was
        # signed in and when, which is more than a repr should decide to do.
        return f"<Session user_id={self.user_id}>"
