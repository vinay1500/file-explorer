"""Account identity.

Deliberately minimal at M2. Nodes need an owner from the start -- adding a
NOT NULL foreign key to a populated table later is a painful migration, and
invariants I2 and I6 both depend on ownership. Authentication itself
(hashing, sessions, CSRF) arrives at M5.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from file_explorer.extensions import Base

# Practical maximum for an address per RFC 3696 erratum 1690.
MAX_EMAIL_LENGTH = 320


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    email: Mapped[str] = mapped_column(String(MAX_EMAIL_LENGTH), nullable=False)

    # No default, and a check constraint that forbids the empty string.
    #
    # It shipped as `server_default=""` at M2, reasoning about the *column*:
    # empty rather than nullable, so the column would never have to be tightened
    # later. The column never needed tightening. **The rows did**, and nothing
    # scheduled that -- so every user row in existence held a hash that no code
    # path could ever verify, and the schema called each one a credentialed
    # account. Gate 1 found it before any login code was written.
    #
    # The failure it would have produced is worth knowing. `argon2.verify("")`
    # raises `InvalidHashError`, which descends from `ValueError` and **not**
    # from `Argon2Error`, so the obvious `except VerificationError` misses it:
    # login answers 500 rather than 401, and that 500 is an existence oracle --
    # an unknown address gives 401, a legacy row gives 500 -- in the status code
    # and, because the empty-hash branch returns before any Argon2 work, in the
    # timing channel as well.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    nodes = relationship("Node", back_populates="owner", passive_deletes=True)

    __table_args__ = (
        # Case-insensitive uniqueness via a functional index rather than the
        # citext type. citext needs CREATE EXTENSION, which not every managed
        # PostgreSQL grants; this works on any installation and is enforced by
        # the database either way.
        #
        # Known divergence, recorded rather than hidden: `lower()` folds
        # different character sets in CI and in production. The deployed
        # database is `C.UTF-8` under PostgreSQL's **builtin** provider, where
        # `lower()` does Unicode simple case mapping; CI's containers are
        # `--locale=C` under **libc**, where only ASCII A-Z are letters. So the
        # two disagree about whether two addresses are one account, in both
        # directions, and the CI assertion that looks like it covers this uses
        # `A@EXAMPLE.COM`, which is pure ASCII and passes either way. Task M1.12
        # closes it by matching production's provider; until then addresses are
        # normalised in Python before they reach this index, which is what makes
        # the divergence not matter in practice.
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        # Not `<> ''`. A constraint that only forbids the empty string cannot
        # tell a hash from the word "hash", and the defect it was written for
        # was a *shape* problem: a value the library cannot parse, stored in a
        # column the schema calls a credential. The prefix is the shape.
        #
        # This deliberately couples the column to argon2id. Moving to another
        # scheme should require a migration that says so, rather than two
        # formats quietly coexisting while one of them stops verifying.
        CheckConstraint(
            "password_hash LIKE '$argon2id$%'", name="ck_users_password_hash_is_argon2id"
        ),
        CheckConstraint("email = btrim(email, ' \t\n\r')", name="ck_users_email_trimmed"),
        CheckConstraint("email <> ''", name="ck_users_email_not_blank"),
    )

    def __repr__(self) -> str:
        # Never the address -- it is personal data (SECURITY §3).
        return f"<User id={self.id}>"
