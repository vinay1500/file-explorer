# 0001 — Adjacency list for the tree

Accepted · 2026-11-14

## Context
A folder tree of unbounded depth in a relational store. The defining interaction is
drag-and-drop, so **moving a subtree is the dominant write**.

## Decision
Adjacency list: each row stores `parent_id`, one self-referential foreign key.

## Consequences
A move is a single-row update — nothing else shifts. Reads of an arbitrary subtree or
ancestry need recursion, which since PostgreSQL 8.4 is one `WITH RECURSIVE` statement
rather than N round trips, bounded by depth and not by table size.

## Alternatives
| | Move cost | Why rejected |
|---|---|---|
| Nested set | renumbers much of the table | the dominant write becomes the most expensive operation |
| Materialised path | rewrites every descendant's path | same, plus storage grows with depth |
| Closure table | delete + reinsert every ancestor pair for the subtree | right for read-heavy ancestry with rare writes; wrong here |

## When this stops being right
A read-heavy permission system doing millions of ancestry checks with rare writes. The
answer then is to **add** a closure table maintained by trigger as a derived index, keeping
the adjacency list as the source of truth — not to replace it.
