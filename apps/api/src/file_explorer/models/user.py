"""Account identity.

Deliberately minimal at M2. Nodes need an owner from the start -- adding a
NOT NULL foreign key to a populated table later is a painful migration, and
invariants I2 and I6 both depend on ownership. Authentication itself
(hashing, sessions, CSRF) arrives at M5.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from file_explorer.extensions import Base

# Practical maximum for an address per RFC 3696 erratum 1690.
MAX_EMAIL_LENGTH = 320


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    email: Mapped[str] = mapped_column(String(MAX_EMAIL_LENGTH), nullable=False)

    # Populated at M5. Empty string until then rather than nullable, so the
    # column never has to be tightened later.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    nodes = relationship("Node", back_populates="owner", passive_deletes=True)

    __table_args__ = (
        # Case-insensitive uniqueness via a functional index rather than the
        # citext type. citext needs CREATE EXTENSION, which not every managed
        # PostgreSQL grants; this works on any installation and is enforced by
        # the database either way.
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
    )

    def __repr__(self) -> str:
        # Never the address -- it is personal data (SECURITY §3).
        return f"<User id={self.id}>"
