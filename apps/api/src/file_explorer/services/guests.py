"""Creating a demo account, and collecting it again.

D-24. The demo is reached by one click, because an interviewer with thirty
seconds will not fill in a sign-up form and a demo nobody can open is not a
demo.

**A guest is exactly a user.** Same table, same foreign keys, same `owner_id`
predicate on every query. Nothing here weakens the isolation the previous
milestone was spent establishing -- which is the whole reason this design was
chosen over a shared demo account or read-only unauthenticated access. Two
guests in two browsers *demonstrate* the isolation rather than claiming it.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import scoped_session

from file_explorer import passwords
from file_explorer.models.user import User

#: Long enough to look around, short enough that an abandoned one costs little.
GUEST_SESSION_LIFETIME = timedelta(hours=2)

#: How long a guest account survives after its last session could have expired.
#: Deliberately longer than the session: a visitor who comes back in an hour
#: should find their tree, and the row is a few hundred bytes.
GUEST_RETENTION = timedelta(hours=24)

#: `.invalid` is reserved by RFC 2606 and can never be a deliverable address, so
#: a guest's email cannot collide with a real one and cannot receive mail by
#: accident. The random part prevents collisions between guests.
GUEST_EMAIL_DOMAIN = "guests.invalid"

#: A tree with something to drag, something to drag it into, and enough depth to
#: show a breadcrumb. Nine nodes: small enough to be free, interesting enough
#: that the first thing a visitor does is move one.
SEED: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Documents",
        "folder",
        (("Invoice 2026-Q1.pdf", "file"), ("Invoice 2026-Q2.pdf", "file")),
    ),
    ("Contracts", "folder", (("Lease.pdf", "file"),)),
    ("Photos", "folder", (("Holiday", "folder"),)),
    ("Archive", "folder", ()),
)


def create_guest(db: DbSession | scoped_session[Any]) -> int:
    """A new guest account with a seeded tree. Returns its id. The caller commits.

    The password hash is a real argon2id hash of a random string that is never
    stored or returned, so the account satisfies the schema's "must be an
    argon2id hash" constraint and **cannot be signed into by password at all**.
    That is the point: the only way into a guest account is the session this
    hands back.
    """
    email = f"guest-{secrets.token_urlsafe(12)}@{GUEST_EMAIL_DOMAIN}".lower()
    user = User(
        email=email,
        password_hash=passwords.hash_password(secrets.token_urlsafe(32)),
        is_guest=True,
    )
    db.add(user)
    db.flush()

    for name, node_type, children in SEED:
        parent_id = db.execute(
            text(
                "INSERT INTO nodes (owner_id, parent_id, name, type) "
                "VALUES (:o, NULL, :n, :t) RETURNING id"
            ),
            {"o": user.id, "n": name, "t": node_type},
        ).scalar_one()
        for child_name, child_type in children:
            db.execute(
                text("INSERT INTO nodes (owner_id, parent_id, name, type) VALUES (:o, :p, :n, :t)"),
                {"o": user.id, "p": parent_id, "n": child_name, "t": child_type},
            )

    return int(user.id)


def is_guest(db: DbSession | scoped_session[Any], user_id: int) -> bool:
    """Whether this account is a demo one. Used to shape what the UI offers."""
    return bool(db.execute(select(User.is_guest).where(User.id == user_id)).scalar_one_or_none())


def purge_guests(db: DbSession | scoped_session[Any], now: datetime | None = None) -> int:
    """Delete guest accounts older than the retention window.

    One statement, and the cascades do the rest: `nodes.owner_id` and
    `sessions.user_id` both cascade, so this removes a guest's tree and any
    session still pointing at it without naming either table.

    Reads `ix_users_is_guest`, which is partial on `is_guest` -- a full index on
    `created_at` would be almost entirely rows this never wants.

    Not scheduled yet. It exists so that scheduling it is a cron entry rather
    than a design, and so the honest answer to "what stops guest accounts
    accumulating" is a function rather than an intention.
    """
    at = (now or datetime.now(UTC)) - GUEST_RETENTION
    result = db.execute(delete(User).where(User.is_guest, User.created_at <= at))
    return int(getattr(result, "rowcount", 0) or 0)
