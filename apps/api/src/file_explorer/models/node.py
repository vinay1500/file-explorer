"""The tree.

One table, one self-referential foreign key. See docs/ARCHITECTURE.md §3 for why
an adjacency list rather than a nested set, closure table or materialised path,
and §4 for the eight invariants this schema is built to hold.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from file_explorer.extensions import Base

MAX_NAME_LENGTH = 255


class NodeType(enum.StrEnum):
    FILE = "file"
    FOLDER = "folder"


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Every query filters on this. Absent in the original schema, which is why
    # GET /api/tree returned every row in the table for every caller (F-08).
    owner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # NULL means root. Not missing data -- a distinct, meaningful state.
    #
    # ON DELETE CASCADE is the fix for F-03: the original relationship declared
    # no cascade at all, so SQLAlchemy's default disassociated children by
    # setting parent_id to NULL. Since NULL means root, deleting a folder
    # silently emptied its contents onto the top level.
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("nodes.id", ondelete="CASCADE"),
        nullable=True,
    )

    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)

    type: Mapped[NodeType] = mapped_column(
        Enum(NodeType, name="node_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    owner = relationship("User", back_populates="nodes")

    children = relationship(
        "Node",
        back_populates="parent",
        # Two foreign keys now point nodes at nodes, so say which one the
        # relationship travels.
        foreign_keys=[parent_id],
        # NOT delete-orphan. That deletes a child on de-association, and
        # setting parent to None -- moving a node to the root -- is
        # de-association. With passive_deletes the children are never loaded,
        # so the database cascade would take the whole subtree. The feature
        # that breaks is move-to-root, which is the one this project exists to
        # fix. Reparenting between two loaded parents is unaffected, so an
        # A-into-B test would never catch it.
        cascade="all, delete-orphan",
        # Let the database do the deleting. Without this SQLAlchemy loads every
        # descendant into memory to delete them one at a time, turning the
        # removal of a large folder into thousands of statements.
        passive_deletes=True,
    )
    parent = relationship(
        "Node", back_populates="children", remote_side=[id], foreign_keys=[parent_id]
    )

    __table_args__ = (
        # I8 -- a node is never its own parent.
        #
        # IS DISTINCT FROM, not !=. For a nullable column `parent_id != id`
        # evaluates to NULL whenever parent_id is NULL, and a CHECK constraint
        # passes on NULL -- so the constraint would silently do nothing for
        # every root node. IS DISTINCT FROM treats NULL as a value and returns
        # an actual boolean.
        CheckConstraint("parent_id IS DISTINCT FROM id", name="not_own_parent"),
        # Rejects empty and ASCII-whitespace-only names. Unicode whitespace
        # is not covered and is not yet handled anywhere -- see the migration.
        CheckConstraint(r"length(btrim(name, E' \t\n\r\f\v')) > 0", name="name_not_blank"),
        # I6 at the database layer -- see the migration for the reasoning.
        UniqueConstraint("id", "owner_id", name="uq_nodes_id_owner"),
        ForeignKeyConstraint(
            ["parent_id", "owner_id"],
            ["nodes.id", "nodes.owner_id"],
            name="fk_nodes_parent_same_owner",
            ondelete="CASCADE",
        ),
        # Every child lookup and every step of the ancestry walk uses this.
        # owner_id first because it is in every WHERE clause in the application,
        # and a composite index can serve a prefix of its columns.
        Index("ix_nodes_owner_parent", "owner_id", "parent_id"),
        # I4 -- sibling names unique within a folder.
        Index(
            "uq_nodes_parent_name",
            "parent_id",
            "name",
            unique=True,
            postgresql_where=text("parent_id IS NOT NULL"),
        ),
        # I2 -- root names unique per owner.
        #
        # Two partial indexes rather than one constraint because PostgreSQL
        # treats NULLs as distinct for uniqueness: a plain
        # UNIQUE (parent_id, name) would permit unlimited duplicate names at
        # the root, since every root row has parent_id NULL and no two NULLs
        # ever conflict. The root case also needs scoping by owner, which a
        # single constraint could not express.
        Index(
            "uq_nodes_root_name",
            "owner_id",
            "name",
            unique=True,
            postgresql_where=text("parent_id IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        # Node names are user content. Log ids, never names (SECURITY §3).
        return f"<Node id={self.id} parent={self.parent_id} type={self.type.value}>"
