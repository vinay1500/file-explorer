"""The sessions table, and a users table that cannot hold a blank password hash.

Revision ID: 0003
Revises: 0002

Two changes that have to land together, because the second one is the reason the
first one is safe.

**The sessions table** implements D-21: the session is an opaque id in a row
rather than a JWT. The reasoning lives in `models/session.py` next to the
columns it explains. What matters here is the shape:

* the primary key is the **SHA-256 of the cookie value**, so a read of this
  table yields nothing a caller can present;
* `ON DELETE CASCADE` from `users`, so deleting an account ends its sessions
  rather than leaving signed credentials that outlive the row they name;
* `csrf_token` in the row, which turns `docs/SECURITY.md` §2.5's double-submit
  into a **synchronizer token** -- bound to the session rather than compared
  with itself. Being stateful is what makes that free.

**Dropping the default on `password_hash`** closes the hole Gate 1 found before
any login code existed. It shipped as `NOT NULL DEFAULT ''`, so every row that
has ever been inserted holds a hash nothing can verify, and the schema calls
each one a credentialed account.

## This migration can fail, and when it does it must not fix it

`_assert_no_blank_hashes` runs first, and refuses rather than repairing. The
repair looks obvious -- delete the rows that cannot be authenticated -- and it
is the one thing this migration must never do: `nodes.owner_id` cascades, so
deleting a placeholder user **deletes that user's whole tree**. On a development
database that is an afternoon; on the demo database it is the demo.

So it stops and names the rows. The operator decides whether they are fixtures
to delete or accounts to reset, which is a judgement a migration does not have.
Same shape as `0002`'s duplicate check, and for the same reason: a sentence
naming the problem is a better thing to meet than a constraint violation
quoting a key tuple.

## Why the timeouts are not this file's problem

Adding a check constraint validates it against every existing row, and that
takes as long as the table is long. The application's connections carry
`statement_timeout=5s`, which is correct for a tree query and far too short for
DDL. Rather than each migration remembering to raise it, `migrations/env.py`
sets a migration-appropriate budget on the migration transaction -- so this
file, and every later one, inherits it without knowing.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Matches `models/session.py`. Duplicated rather than imported, because a
# migration has to keep describing the schema it created even after the model
# moves on -- importing the model would make this file silently follow it.
DIGEST_BYTES = 32
CSRF_MAX_LENGTH = 64


def _assert_no_blank_hashes() -> None:
    """Fail readably rather than deleting somebody's tree."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("""
        SELECT id, length(email) AS email_length
          FROM users
         WHERE password_hash NOT LIKE '$argon2id$%'
         ORDER BY id
         LIMIT 20
        """)
    ).all()
    if rows:
        ids = ", ".join(str(r.id) for r in rows)
        raise RuntimeError(
            "users rows hold a password_hash that is not an argon2id hash, and this "
            "migration will not guess what "
            f"to do with them: ids {ids}. Each one is an account nothing can authenticate. "
            "Delete them only if you know they are fixtures -- nodes.owner_id cascades, so "
            "deleting a user deletes that user's whole tree -- otherwise set a real argon2id "
            "hash and run this again. Addresses are not printed here; they are personal data "
            "and this message may end up in a deployment log."
        )


def upgrade() -> None:
    _assert_no_blank_hashes()

    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.LargeBinary(length=DIGEST_BYTES), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("csrf_token", sa.String(length=CSRF_MAX_LENGTH), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("token_hash", name="pk_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_sessions_expiry_after_creation"),
        sa.CheckConstraint("length(csrf_token) >= 16", name="ck_sessions_csrf_long_enough"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.alter_column("users", "password_hash", server_default=None)
    # The prefix rather than `<> ''`: the defect was a value the library cannot
    # parse, and "not empty" does not describe that. See `models/user.py`.
    op.create_check_constraint(
        "ck_users_password_hash_is_argon2id", "users", "password_hash LIKE '$argon2id$%'"
    )
    op.create_check_constraint("ck_users_email_not_blank", "users", "email <> ''")
    op.create_check_constraint(
        "ck_users_email_trimmed", "users", "email = btrim(email, ' \\t\\n\\r')"
    )


def downgrade() -> None:
    # Reversible, and the default comes back with it. A downgrade that left the
    # constraint in place would make the schema un-seedable by anything written
    # against the old shape, which is the whole point of being able to go back.
    op.drop_constraint("ck_users_email_trimmed", "users", type_="check")
    op.drop_constraint("ck_users_email_not_blank", "users", type_="check")
    op.drop_constraint("ck_users_password_hash_is_argon2id", "users", type_="check")
    op.alter_column("users", "password_hash", server_default="")

    op.drop_index("ix_sessions_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
