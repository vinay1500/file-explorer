"""SQL for the tree, including the recursive walks.

Every query here takes an `owner_id` and applies it in **both** the anchor and the
recursive term of each CTE. Filtering only the anchor lets the walk climb out of
the caller's tree: attach a node under a guessed foreign folder and an ancestry
query then returns that folder's names, even though the ownership check on the
starting node passed.

Every walk is also depth-bounded. The CTEs are `UNION ALL`, so on cyclic data an
unbounded walk allocates until it exhausts memory or hits a timeout — and the
cycle detector would be the first casualty of the cycle it exists to detect.

All parameters are bound. Nothing here is composed with an f-string.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from file_explorer.errors import TreeTooDeep

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NodeRow:
    id: int
    parent_id: int | None
    name: str
    type: str


# Ancestors of :node_id, nearest first, including the node itself at depth 0.
# Serves both the cycle check and breadcrumbs.
_ANCESTRY = text("""
    WITH RECURSIVE ancestry AS (
        SELECT id, parent_id, name, type, 0 AS depth
          FROM nodes
         WHERE id = :node_id
           AND owner_id = :owner_id
        UNION ALL
        SELECT n.id, n.parent_id, n.name, n.type, a.depth + 1
          FROM nodes n
          JOIN ancestry a ON n.id = a.parent_id
         WHERE n.owner_id = :owner_id
           AND a.depth < :max_depth
    )
    SELECT id, parent_id, name, type, depth FROM ancestry ORDER BY depth
""")

# How many levels hang below :node_id. 0 for a leaf. Needed because the depth
# bound on a move is depth(destination) + 1 + height(subtree), and an ascending
# walk alone never sees the subtree being carried.
_SUBTREE_HEIGHT = text("""
    WITH RECURSIVE descendants AS (
        SELECT id, 0 AS depth
          FROM nodes
         WHERE id = :node_id
           AND owner_id = :owner_id
        UNION ALL
        SELECT n.id, d.depth + 1
          FROM nodes n
          JOIN descendants d ON n.parent_id = d.id
         WHERE n.owner_id = :owner_id
           AND d.depth < :max_depth
    )
    SELECT COALESCE(MAX(depth), 0) FROM descendants
""")

# Locks the node being moved together with the destination's ancestry chain.
#
# FOR NO KEY UPDATE, not FOR UPDATE: FOR UPDATE conflicts with FOR KEY SHARE,
# the lock an INSERT takes on its parent for the foreign-key check, so holding a
# chain under FOR UPDATE would block unrelated creates underneath it. NO KEY
# UPDATE still excludes other moves, which is all the cycle argument needs.
#
# ORDER BY id so every transaction takes the same lock order. That is a
# convention, not a guarantee -- a concurrent cascading delete locks in scan
# order and can still raise 40P01, so the caller retries on deadlock.
_LOCK_CHAIN = text("""
    WITH RECURSIVE ancestry AS (
        SELECT id, parent_id, 0 AS depth
          FROM nodes
         WHERE id = :new_parent_id
           AND owner_id = :owner_id
        UNION ALL
        SELECT n.id, n.parent_id, a.depth + 1
          FROM nodes n
          JOIN ancestry a ON n.id = a.parent_id
         WHERE n.owner_id = :owner_id
           AND a.depth < :max_depth
    )
    SELECT id
      FROM nodes
     WHERE owner_id = :owner_id
       AND (id = :node_id OR id IN (SELECT id FROM ancestry))
     ORDER BY id
       FOR NO KEY UPDATE
""")


# Locks a node together with everything above it. `create` holds this over its
# depth measurement: without it a concurrent move can re-parent the chain deeper
# between the measurement and the insert, and the new node lands past the cap.
#
# Same FOR NO KEY UPDATE reasoning as _LOCK_CHAIN -- and the same ORDER BY id,
# so a create and a move contending for one chain agree on lock order.
_LOCK_ANCESTRY = text("""
    WITH RECURSIVE ancestry AS (
        SELECT id, parent_id, 0 AS depth
          FROM nodes
         WHERE id = :node_id
           AND owner_id = :owner_id
        UNION ALL
        SELECT n.id, n.parent_id, a.depth + 1
          FROM nodes n
          JOIN ancestry a ON n.id = a.parent_id
         WHERE n.owner_id = :owner_id
           AND a.depth < :max_depth
    )
    SELECT id
      FROM nodes
     WHERE owner_id = :owner_id
       AND id IN (SELECT id FROM ancestry)
     ORDER BY id
       FOR NO KEY UPDATE
""")


# Counts a subtree, and reports how deep it had to go to do it. The depth comes
# back so the caller can tell a complete count from a truncated one -- the same
# argument as `ancestry`: a bounded walk that returns a short answer silently is
# worse than one that fails.
_SUBTREE_SIZE = text("""
    WITH RECURSIVE descendants AS (
        SELECT id, 0 AS depth
          FROM nodes
         WHERE id = :node_id
           AND owner_id = :owner_id
        UNION ALL
        SELECT n.id, d.depth + 1
          FROM nodes n
          JOIN descendants d ON n.parent_id = d.id
         WHERE n.owner_id = :owner_id
           AND d.depth < :max_depth
    )
    SELECT count(*), COALESCE(MAX(depth), 0) FROM descendants
""")

# One row, locked. `delete` holds this over its count.
#
# It is a single row rather than a chain, and that is enough because `create`
# locks its parent's ENTIRE ancestry -- so a create anywhere beneath this node
# already conflicts here, and the count cannot grow underneath the delete.
_LOCK_ONE = text("""
    SELECT id
      FROM nodes
     WHERE id = :node_id
       AND owner_id = :owner_id
       FOR NO KEY UPDATE
""")


class NodeRepository:
    def __init__(self, session: Session, max_depth: int) -> None:
        self._s = session
        self._max_depth = max_depth

    def get(self, owner_id: int, node_id: int) -> NodeRow | None:
        """One node, or None.

        Scoped in the WHERE clause rather than checked afterwards, so forgetting
        the check returns nothing instead of returning someone else's row. The
        caller turns None into 404 -- never 403, which would confirm the row
        exists and make sequential ids enumerable.
        """
        row = self._s.execute(
            text(
                "SELECT id, parent_id, name, type FROM nodes "
                "WHERE id = :node_id AND owner_id = :owner_id"
            ),
            {"node_id": node_id, "owner_id": owner_id},
        ).one_or_none()
        return None if row is None else NodeRow(row[0], row[1], row[2], str(row[3]))

    def ancestry(self, owner_id: int, node_id: int) -> list[tuple[int, int | None, str, str, int]]:
        """`node_id` and its ancestors, nearest first. Breadcrumbs read this.

        Raises `TreeTooDeep` if the walk hit its bound. The CTE stops at
        `max_depth`, so on an over-deep chain it returns a **partial** answer
        with no error -- and a partial ancestry is worse than no ancestry,
        because `is_ancestor_of` would then miss a genuine ancestor and the
        cycle check would fail open. Detecting the truncation turns a silent
        wrong answer into a loud one.

        A legal node sits at level `max_depth` or less, so its ancestry is at
        most `max_depth` rows. The recursive term runs while `depth <
        max_depth`, so it can emit one row beyond that -- which means more than
        `max_depth` rows can only come from data that already breaks I7.
        """
        rows = [
            (r[0], r[1], r[2], str(r[3]), r[4])
            for r in self._s.execute(
                _ANCESTRY,
                {"node_id": node_id, "owner_id": owner_id, "max_depth": self._max_depth},
            ).all()
        ]
        if len(rows) > self._max_depth:
            log.error(
                "ancestry walk hit its depth bound; stored data breaks I7",
                extra={"node_id": node_id, "max_depth": self._max_depth},
            )
            raise TreeTooDeep
        return rows

    def subtree_height(self, owner_id: int, node_id: int) -> int:
        """Levels below `node_id`; 0 for a leaf.

        Same truncation argument as `ancestry`. A root can legally carry a
        height of `max_depth - 1`; reaching `max_depth` means the descending
        walk was cut short and the number returned is a floor, not a height.
        Using a floor in the move bound would let an over-deep subtree move
        anywhere.
        """
        height = int(
            self._s.execute(
                _SUBTREE_HEIGHT,
                {"node_id": node_id, "owner_id": owner_id, "max_depth": self._max_depth},
            ).scalar_one()
        )
        if height >= self._max_depth:
            log.error(
                "subtree walk hit its depth bound; stored data breaks I7",
                extra={"node_id": node_id, "max_depth": self._max_depth},
            )
            raise TreeTooDeep
        return height

    def lock_move_participants(
        self, owner_id: int, node_id: int, new_parent_id: int | None
    ) -> list[int]:
        """Take row locks for a move. Returns the locked ids.

        `new_parent_id` may be None -- a move to the root. The ancestry CTE then
        matches nothing (`id = NULL` is never true), so this reduces to locking
        the moved node alone. That is correct but worth stating: the root has no
        row, so there is nothing else to serialise against, and uniqueness at
        the root is left to `uq_nodes_root_name` rather than to a lock.
        """
        return [
            r[0]
            for r in self._s.execute(
                _LOCK_CHAIN,
                {
                    "node_id": node_id,
                    "new_parent_id": new_parent_id,
                    "owner_id": owner_id,
                    "max_depth": self._max_depth,
                },
            ).all()
        ]

    def is_ancestor_of(self, owner_id: int, candidate_id: int, node_id: int) -> bool:
        """Is `candidate_id` on the path from `node_id` up to the root?

        **Issue this only after the locks are held, as its own statement.**
        `FOR NO KEY UPDATE` does not refresh a recursive CTE: `LockRows` sits
        above the plan and EvalPlanQual re-checks only the outer qual. Reusing
        the rows the lock query returned -- the obvious optimisation -- lets two
        moves across separate trees commit a 4-cycle.
        """
        return bool(
            self._s.execute(
                text("""
                WITH RECURSIVE ancestry AS (
                    SELECT id, parent_id, 0 AS depth
                      FROM nodes
                     WHERE id = :node_id
                       AND owner_id = :owner_id
                    UNION ALL
                    SELECT n.id, n.parent_id, a.depth + 1
                      FROM nodes n
                      JOIN ancestry a ON n.id = a.parent_id
                     WHERE n.owner_id = :owner_id
                       AND a.depth < :max_depth
                )
                SELECT EXISTS (SELECT 1 FROM ancestry WHERE id = :candidate_id)
                """),
                {
                    "node_id": node_id,
                    "candidate_id": candidate_id,
                    "owner_id": owner_id,
                    "max_depth": self._max_depth,
                },
            ).scalar_one()
        )

    def lock_ancestry(self, owner_id: int, node_id: int) -> list[int]:
        """Lock `node_id` and every node above it. Returns the locked ids.

        `create` calls this before measuring depth. An empty list means the
        node is gone or was never the caller's.
        """
        return [
            r[0]
            for r in self._s.execute(
                _LOCK_ANCESTRY,
                {"node_id": node_id, "owner_id": owner_id, "max_depth": self._max_depth},
            ).all()
        ]

    def insert_node(self, owner_id: int, parent_id: int | None, name: str, node_type: str) -> int:
        """Insert one node, returning its id.

        `owner_id` is written from the actor, never taken from the request, so
        a node cannot be created into someone else's tree even if every check
        above were removed -- and the composite foreign key then refuses a
        parent belonging to anyone else.

        The cast is explicit because `type` is an enum: a bound parameter
        arrives as text and PostgreSQL will not coerce it in an INSERT without
        being told to.
        """
        return int(
            self._s.execute(
                text("""
                INSERT INTO nodes (owner_id, parent_id, name, type)
                VALUES (:owner_id, :parent_id, :name, CAST(:type AS node_type))
                RETURNING id
                """),
                {
                    "owner_id": owner_id,
                    "parent_id": parent_id,
                    "name": name,
                    "type": node_type,
                },
            ).scalar_one()
        )

    def set_name(self, owner_id: int, node_id: int, name: str) -> int:
        """Rename one node. Returns rows affected.

        Scoped by `owner_id` in the WHERE clause for the same reason
        `set_parent` is: the guard has to survive the checks above being
        deleted. 0 rows is a 404, not a success.
        """
        result = self._s.execute(
            text(
                "UPDATE nodes SET name = :name, updated_at = now() "
                "WHERE id = :node_id AND owner_id = :owner_id"
            ),
            {"node_id": node_id, "name": name, "owner_id": owner_id},
        )
        return cast("CursorResult[Any]", result).rowcount

    def lock_one(self, owner_id: int, node_id: int) -> bool:
        """Lock a single node. False if it is gone or was never the caller's."""
        return (
            self._s.execute(_LOCK_ONE, {"node_id": node_id, "owner_id": owner_id}).one_or_none()
            is not None
        )

    def subtree_size(self, owner_id: int, node_id: int) -> int:
        """How many nodes `node_id` carries, counting itself. 0 if it is not there.

        Raises `TreeTooDeep` on a truncated walk, for the same reason
        `subtree_height` does: an undercount here would be reported to the
        caller as the number of things deleted, which is a lie about data that
        is already gone.
        """
        count, deepest = self._s.execute(
            _SUBTREE_SIZE,
            {"node_id": node_id, "owner_id": owner_id, "max_depth": self._max_depth},
        ).one()
        return int(count)

    def delete_node(self, owner_id: int, node_id: int) -> int:
        """Delete one node. Returns rows directly affected -- 1 or 0.

        The subtree goes with it through `ON DELETE CASCADE`, which is I3 and
        is the database's job, not this method's. The original schema had no
        cascade, so SQLAlchemy nulled each child's `parent_id` instead -- and
        NULL means root, so deleting a folder emptied it onto the top level.

        The rowcount counts only this row; the cascade is invisible to it.
        That is why the caller counts the subtree beforehand rather than
        reading a number back from here.
        """
        result = self._s.execute(
            text("DELETE FROM nodes WHERE id = :node_id AND owner_id = :owner_id"),
            {"node_id": node_id, "owner_id": owner_id},
        )
        return cast("CursorResult[Any]", result).rowcount

    def set_parent(self, owner_id: int, node_id: int, new_parent_id: int | None) -> int:
        """Re-parent one node. Returns rows affected.

        `owner_id` is in the WHERE clause, not assumed from an earlier read, so
        this cannot move a node the caller does not own even if every check
        above were removed. The rowcount is returned because 0 rows is a 404,
        not a success -- the original code reported `move(999999, 7)` as 200.
        """
        result = self._s.execute(
            text(
                "UPDATE nodes SET parent_id = :new_parent_id, updated_at = now() "
                "WHERE id = :node_id AND owner_id = :owner_id"
            ),
            {"node_id": node_id, "new_parent_id": new_parent_id, "owner_id": owner_id},
        )
        # Session.execute is typed as returning Result, which does not declare
        # rowcount. For a DML statement it is always a CursorResult, which does.
        return cast("CursorResult[Any]", result).rowcount
