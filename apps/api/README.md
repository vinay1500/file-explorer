# apps/api

The Flask API. Owns the tree: creation, renaming, moving, deletion, and the invariants that keep
it a tree.

> **Being rebuilt.** The original four files from April 2025 have been retired; the schema and
> its constraints are in place and verified in CI. The move algorithm, the API layer and the
> test suite do not exist yet.

## What the original version got wrong

Kept here as the record of why each piece of the rebuild exists. The code itself is gone — see
`git log` — and the items marked **fixed** are enforced by the schema and asserted in CI:

- **Moving a folder into its own descendant creates a cycle.** `move_node` rejects only
  `new_parent_id == id`. The deeper case detaches the whole subtree from the root; no error is
  raised and no row is corrupted, but the data stops being reachable.
- **Deleting a folder scattered its contents to the root**, because the relationship declared no
  cascade and `parent_id IS NULL` means root. *Fixed* — `ON DELETE CASCADE`, asserted in CI.
- **`GET /create-tables` ran DDL unauthenticated.** *Fixed* — the route is gone and CI fails if
  any route performs DDL.
- **No request validation.** A missing field raises `KeyError` and returns 500.
- **`GET /api/tree` returns every row in the table.** No scoping, no pagination, no depth limit.
- **CORS reflects any origin.** `CORS(app)` with no allowlist.
- **No index on `parent_id`, no unique constraint on `(parent_id, name)`.** *Fixed.*
- **`PyMySQL` was pinned** in a PostgreSQL project, left over from an abandoned MySQL phase.
  *Fixed* — and the driver is now psycopg 3.

The target schema, the eight invariants and the move algorithm are in
[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md).
