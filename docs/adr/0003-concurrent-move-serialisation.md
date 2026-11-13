# 0003 — Serialising concurrent moves

Accepted · 2026-11-14

## Context
Alice moves `Docs` into `Work`; Bob simultaneously moves `Work` into `Docs`. Both are
siblings at the root, so each move is individually legal and **each ancestry check in
ADR 0002 passes honestly**. Both commit. The result is a cycle.

This is **write skew**: each transaction reads a set of rows, decides from them, and writes
to a *different* row. Neither overwrites the other, so no conflict is detected, yet the
combined result violates a constraint spanning both. Under `READ COMMITTED` nothing
prevents it.

## Decision
Run the check and the write in one transaction that first takes
`SELECT … ORDER BY id FOR NO KEY UPDATE` over the destination's ancestry chain **and the
node being moved**, then re-runs the ancestry walk as a **fresh statement** before updating.

Three details carry the weight:

1. **The lock on the moved node is the load-bearing one.** Two moves that could jointly
   close a cycle always have one's moved node inside the other's destination ancestry, so
   their lock sets necessarily intersect — including across multi-root forests and
   three-way races. It is not the destination chain that makes this safe.
2. **Step two must be a new statement.** `FOR UPDATE` does not refresh a recursive CTE:
   `LockRows` sits above the plan and EvalPlanQual re-checks only the outer qual. Reusing
   the rows returned by the locking select — the obvious optimisation — admits a 4-cycle
   across two separate trees.
3. **`FOR NO KEY UPDATE`, not `FOR UPDATE`.** `FOR UPDATE` conflicts with `FOR KEY SHARE`,
   the lock an `INSERT` takes on its parent for the foreign-key check, so holding a chain
   would block unrelated creates underneath it. `FOR NO KEY UPDATE` still excludes other
   moves, so the argument above is unchanged.

## Consequences
`ORDER BY id` gives every transaction the same lock order, so two overlapping chains queue
instead of deadlocking. **This is a convention, not a guarantee** — a concurrent cascading
delete locks in scan order and can still produce `40P01`, so the caller retries on
deadlock. The lock set is also computed from an unlocked read, which is why step two
re-reads: the honest description of this design is *an optimistic check plus best-effort
chain serialisation under row locks*, not "a row-locked transaction".

## Alternatives
- **`SERIALIZABLE` + retry on `40001`.** Provably correct and three lines. Rejected because
  it pushes a retry loop into every caller — a serialization failure means "try again", not
  "you were wrong" — and makes every transaction in the application pay for a guarantee only
  `move` needs. A defensible choice; the trade-off is scope of cost, not correctness.
- **`pg_advisory_xact_lock(owner_id)`.** Simpler and genuinely attractive: every tree belongs
  to exactly one user, so one lock per owner serialises all of that user's moves. Rejected
  because it serialises *all* writes for an owner, including moves in unrelated parts of
  their tree, and because the row locks are needed anyway to keep the ancestry stable. Worth
  revisiting if lock contention ever shows up in practice.
- **Application-level mutex.** Does not survive more than one process.
