# apps/api

The Flask API. Owns the tree: creation, renaming, moving, deletion, and the invariants that keep
it a tree.

> **Being rebuilt.** This is still the original code from April 2025 — four files and no tests.
> The schema, the move algorithm and the test suite arrive at M2.

## Known problems in the current code

Carried over deliberately — each is the reason for a specific piece of the rebuild:

- **Moving a folder into its own descendant creates a cycle.** `move_node` rejects only
  `new_parent_id == id`. The deeper case detaches the whole subtree from the root; no error is
  raised and no row is corrupted, but the data stops being reachable.
- **Deleting a folder scatters its contents to the root.** The `children` relationship declares
  no cascade, so SQLAlchemy nulls each child's `parent_id` — and `parent_id IS NULL` means root.
- **`GET /create-tables` runs DDL unauthenticated.** Removed at M0 from the deployment; removed
  from the code at M2.
- **No request validation.** A missing field raises `KeyError` and returns 500.
- **`GET /api/tree` returns every row in the table.** No scoping, no pagination, no depth limit.
- **CORS reflects any origin.** `CORS(app)` with no allowlist.
- **No index on `parent_id`, no unique constraint on `(parent_id, name)`.**
- **`PyMySQL` is pinned** in a PostgreSQL project, left over from an abandoned MySQL phase.

The target schema, the eight invariants and the move algorithm are in
[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md).
