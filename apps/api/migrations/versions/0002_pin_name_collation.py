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

A note on whether the rebuild can fail. The comparison argument says it cannot:
the default collations are deterministic, so they already consider two
different byte strings unequal, and `"C"` makes no more pairs equal than they
did. That argument is about which pairs *compare* equal and says nothing about
which rows are *already stored* -- and the opening paragraph above describes
exactly the population where that matters. If collation drift has already let a
duplicate past a corrupt index, `CREATE UNIQUE INDEX` finds it and aborts.

So the one population this migration exists for is the one it can fail on.
`_assert_no_duplicates` checks first and says which rows to look at, because
"could not create unique index" with a key tuple is a worse thing to meet at
three in the morning than a sentence naming the problem.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _assert_no_duplicates() -> None:
    """Fail readably if the data already breaks what we are about to enforce."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("""
        SELECT 'uq_nodes_parent_name' AS idx, parent_id::text AS scope,
               min(name) AS example, count(*) AS n
          FROM nodes
         WHERE parent_id IS NOT NULL
         GROUP BY parent_id, name COLLATE "C"
        HAVING count(*) > 1
         UNION ALL
        SELECT 'uq_nodes_root_name', owner_id::text, min(name), count(*)
          FROM nodes
         WHERE parent_id IS NULL
         GROUP BY owner_id, name COLLATE "C"
        HAVING count(*) > 1
         LIMIT 20
        """)
    ).all()
    if rows:
        detail = "; ".join(f"{r[0]} scope={r[1]} name={r[2]!r} x{r[3]}" for r in rows)
        raise RuntimeError(
            "cannot pin the name collation: these rows are already duplicates under "
            f'COLLATE "C" and must be renamed or removed first -- {detail}'
        )


def upgrade():
    _assert_no_duplicates()

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
