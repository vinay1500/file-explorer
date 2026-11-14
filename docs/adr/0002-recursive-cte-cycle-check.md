# 0002 — Cycle prevention by recursive-CTE ancestry check

Accepted · 2026-11-14

## Context
Moving a node under its own descendant produces a cycle. No row is corrupted and no error
is raised, but the subtree stops being reachable from the root — to a user, the data has
vanished. A `CHECK` constraint cannot see it: the illegal condition is a property of a path
across many rows.

## Decision
Before a move, walk upward from the destination with `WITH RECURSIVE` and reject if the
node being moved appears in that ancestry. The walk carries an explicit depth bound so
malformed data cannot make it run away.

## Consequences
O(depth) with one index lookup per level, independent of table size. One statement, no loop.
The same query serves breadcrumbs.

## Alternatives
- **`CHECK` constraint** — impossible; single-row scope. The trivial self-parent case *is* a
  check constraint (`parent_id IS DISTINCT FROM id`), because that one does fit in a row.
- **Trigger running the same query** — works, but the check needs to produce a specific,
  explainable 409 and to sit inside the same transaction as the lock, so it belongs in the
  service layer.
- **Closure table with a self-pair unique constraint** — would make a cycle a constraint
  violation, at the cost of ADR 0001's write profile.

## Not sufficient on its own
Two concurrent moves can each pass this check against a tree that is still valid at the
moment they look. See ADR 0003.
