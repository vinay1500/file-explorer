"""Pin the collation on every index that orders or compares a name.

Revision ID: 0002
Revises: 0001

A text index built under one collation and queried under another disagrees with
itself. The classic case is the glibc 2.28 change, which altered sort order for
most locales: indexes built before the upgrade were silently wrong afterwards,
so unique indexes stopped catching duplicates and ordered scans returned rows in
an order the planner no longer believed.

That matters twice here.

* `uq_nodes_parent_name` and `uq_nodes_root_name` enforce I4 and I2. An index
  that disagrees with the comparison operator can let a duplicate through.
* Both also serve the keyset pagination added with `list_children` and
  `list_roots`. Keyset asks "give me the rows after this one" in sort order, so
  if the stored order and the comparison disagree, pages **skip rows** -- and
  they skip silently, because every individual page still looks plausible.

`"C"` is byte order: no locale, no library version, no drift. The cost is that
uppercase sorts before lowercase, so `Zebra` comes before `apple`. That is a
display concern and belongs to the client, which can order however it likes --
the stored order only has to be *stable*, because that is what pagination and
uniqueness depend on.

Recreating a unique index under `"C"` cannot fail on existing data. The default
collations are deterministic, so they already consider two different byte
strings unequal; `"C"` makes no more pairs equal than they did. It can only
ever be more permissive, never less.
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    # Dropped and recreated rather than altered: PostgreSQL has no
    # ALTER INDEX ... COLLATE, and the collation is a property of the indexed
    # column inside the index definition.
    op.drop_index("uq_nodes_parent_name", table_name="nodes")
    op.execute(
        'CREATE UNIQUE INDEX uq_nodes_parent_name ON nodes (parent_id, name COLLATE "C") '
        "WHERE parent_id IS NOT NULL"
    )

    op.drop_index("uq_nodes_root_name", table_name="nodes")
    op.execute(
        'CREATE UNIQUE INDEX uq_nodes_root_name ON nodes (owner_id, name COLLATE "C") '
        "WHERE parent_id IS NULL"
    )


def downgrade():
    op.drop_index("uq_nodes_root_name", table_name="nodes")
    op.execute(
        "CREATE UNIQUE INDEX uq_nodes_root_name ON nodes (owner_id, name) WHERE parent_id IS NULL"
    )

    op.drop_index("uq_nodes_parent_name", table_name="nodes")
    op.execute(
        "CREATE UNIQUE INDEX uq_nodes_parent_name ON nodes (parent_id, name) "
        "WHERE parent_id IS NOT NULL"
    )
