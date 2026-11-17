"""Mark an account as a demo guest, so the sweeper can collect it.

Revision ID: 0004
Revises: 0003

D-24: the demo is reached by one click rather than a sign-up form, because an
interviewer with thirty seconds will not register and a demo nobody can open is
not a demo.

The alternative designs both cost something this one does not. A single shared
demo account means the first visitor who deletes everything ruins it for the
next. Read-only unauthenticated access punches a hole in the rule M5 was spent
establishing -- every route requires a session, every query is owner-scoped --
and "except this one" is the sentence an interviewer asks about.

A guest is **exactly a user**. Same table, same foreign keys, same owner
predicate on every query. The only thing this column changes is that something
is allowed to delete them later.

A column rather than a convention on the address. `email LIKE '%@guests.invalid'`
would work today and is a rule somebody relaxes in six months; a boolean is a
fact about the row.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # Partial, because guests are the minority and the only query that uses this
    # asks for them specifically. A full index would be almost entirely rows the
    # sweeper never wants.
    op.create_index(
        "ix_users_is_guest",
        "users",
        ["created_at"],
        postgresql_where=sa.text("is_guest"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_is_guest", table_name="users")
    op.drop_column("users", "is_guest")
