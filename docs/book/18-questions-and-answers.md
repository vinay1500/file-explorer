# Chapter 18 · Questions and answers

> Volume V · The Defence
> Written at M9 · last verified 2026-11-19

Q20 to Q33 live in the chapters they belong to — the API contract, identity and isolation, and
testing each end with their own. These are the first nineteen: the schema, the invariants, the
failures, and the queries underneath them.

Every answer here is short on purpose. An interview answer that runs three minutes is a lecture;
these are the thirty-second versions, each with the chapter that has the long one.

---

## The model

**Q1. Why an adjacency list rather than nested sets or a closure table?**
Because the dominant write is a *move*, and in an adjacency list a move is one row changing one
column. Nested sets renumber much of the table, materialised paths rewrite every descendant, and
a closure table deletes and reinserts a pair for every ancestor of every node in the subtree. I
pay for that choice in reads, where ancestry and subtree need recursion — one `WITH RECURSIVE`
statement, bounded by depth rather than table size. → [Chapter 2](02-four-ways-to-store-a-tree.md)

**Q2. What does `parent_id = NULL` mean, and is that a good idea?**
It means root. It is the usual choice and it has one sharp edge worth naming: "detach this
child" and "promote this child to the top level" become the same write, which is exactly how
deleting a folder scattered its contents across the root in the version that shipped. The
alternative — a synthetic root row per owner — removes that ambiguity and adds a row that every
query has to remember to skip. → [Chapter 6](06-the-orphans.md)

**Q3. Why is `type` an enum in the database rather than a boolean `is_folder`?**
Because a boolean is a schema that can only ever answer one question. The enum has two values
today and the migration to add a third is a value, not a column. It also reads correctly in a
`psql` session, which is where you are when something has gone wrong.

**Q4. Why `BIGINT` ids and not UUIDs?**
Ordering and locality. Ids here are used for cursor pagination and for deterministic lock
ordering during a concurrent move, and both want a total order that matches insertion. A random
UUID gives neither, and costs index locality on every child lookup. If these ids were ever
exposed as guessable handles to other tenants it would be a different trade — they are not,
because every query is scoped by owner anyway. → [Chapter 12](12-identity-and-isolation.md)

---

## The invariants

**Q5. How many invariants are there and where do they live?**
Eight. Four are enforced by the database — the foreign key, the composite key that forbids a
cross-owner attach, and two partial unique indexes for names. Four are enforced by the service
inside a transaction, because they are statements about paths rather than rows: no cycle, depth
within 64, the per-owner node ceiling, and that a file is never a parent. Each one names the
test that proves it. → [Chapter 4](04-the-eight-invariants.md)

**Q6. Why two partial unique indexes for names instead of one constraint?**
Because PostgreSQL treats `NULL`s as distinct for uniqueness, so a plain
`UNIQUE (parent_id, name)` permits unlimited duplicate names at the root — every root row has
`parent_id NULL`, and no two `NULL`s ever conflict. So: `uq_nodes_parent_name` on
`(parent_id, name)` where `parent_id IS NOT NULL`, and `uq_nodes_root_name` on
`(owner_id, name)` where `parent_id IS NULL`. The root case also has to be scoped by owner,
which one constraint could not express.

**Q7. The foreign key is on `(parent_id, owner_id)` referencing `(id, owner_id)`. Why the owner column in a parent link?**
So that attaching a node to another tenant's folder is impossible at the schema level rather
than conditional on every query remembering a `WHERE`. A composite key makes "my child under
your parent" unrepresentable. It is the one isolation guarantee that does not depend on
application code being right. → [Chapter 12](12-identity-and-isolation.md)

**Q8. Why cap depth at 64 at all?**
Because an uncapped tree is a denial-of-service vector: a script can create a hundred thousand
nested folders through the ordinary API, and any recursive query over that chain holds a
connection and allocates memory proportional to depth. The cap is also what keeps the cycle
check *sound* — both recursive walks stop at the same value, so a tree deeper than the cap would
make an ancestry walk return a partial chain and miss a real ancestor.

---

## The failures

**Q9. Walk me through the cycle bug.**
`move_node` rejected only `new_parent_id == id`. Nothing checked whether the destination was a
descendant of the thing being moved, so dragging a folder into its own child wrote a perfectly
valid row: both ends existed, the foreign key was satisfied, nothing raised. The subtree stopped
being reachable from any root, so to the user the folder had vanished; to the database nothing
was wrong. The fix is a recursive descendant check **inside the transaction that performs the
move**. → [Chapter 5](05-the-cycle.md)

**Q10. And the obvious version of that fix is incomplete. Why?**
Because a check that runs before the lock is a statement about the tree as it was, not as it is.
Two concurrent moves can each pass their own check and produce a cycle together. The move locks
the ancestry chain in a deterministic order — ascending id — and re-reads it after the lock, so
the check and the write see the same tree, and two transactions that would conflict queue
instead of deadlocking. → [Chapter 10](10-the-race-nobody-sees.md)

**Q11. Why does the orphan bug make deletion dangerous rather than merely wrong?**
Because the failure mode is an apparent success. The ORM relationship declared no cascade, so
SQLAlchemy's default de-associated children on delete: `UPDATE nodes SET parent_id = NULL`. In
this schema that is not an orphan, it is a promotion to root. Deleting a folder with forty files
in it produced forty files at the top level, no error, no confirmation dialog, one click.
→ [Chapter 6](06-the-orphans.md)

**Q12. You fixed that in two places. Is that not redundant?**
No, they cover different things. `ON DELETE CASCADE` in the database is the guarantee — it holds
for a `DELETE` typed into `psql`, for a migration, for anything. The ORM's `passive_deletes=True`
stops SQLAlchemy from loading every descendant into memory to delete them one at a time, and
keeps the in-request identity map consistent with what the database just did. Remove either and
something real breaks.

**Q13. What was the outage, and why did monitoring not catch it?**
The database instance had been deleted, so every request that touched it returned 500 for
seventeen months. Monitoring did not catch it because the health endpoint returned a hardcoded
string and never touched the database: it could not fail while the process was alive. A health
check that does not exercise its dependency is decoration. The current one runs `SELECT 1` and
answers 503 when that fails. → [Chapter 7](07-the-silent-outage.md)

---

## The queries

**Q14. Why `WITH RECURSIVE` rather than a loop in the application?**
One round trip instead of N, and the planner gets to see the whole thing. The loop version is
also where depth limits get forgotten, because the bound lives in application code rather than
in the statement. Both recursive queries here carry an explicit depth bound and an owner
predicate in the recursive term, not only in the anchor. → [Chapter 9](09-recursive-ctes.md)

**Q15. What stops a recursive query running away?**
Three things: the depth cap inside the CTE, the per-owner node ceiling of 50,000, and a
statement timeout on the connection. The first bounds a path, the second bounds the fan-out, and
the third bounds everything else — including the query nobody thought about.

**Q16. Why cursor pagination instead of `LIMIT`/`OFFSET`?**
Because `OFFSET` re-reads and discards rows, so deep pages get slower, and because a row
inserted behind the cursor shifts every subsequent page — a caller paging through a tree would
see an item twice or not at all. The cursor encodes the sort key of the last row, is opaque, and
carries no identity: `test_a_cursor_carries_no_identity` and
`test_a_row_inserted_behind_the_cursor_does_not_shift_the_page`.
→ [Chapter 11](11-the-api-contract.md)

**Q17. What is the index strategy, in one sentence?**
`(owner_id, parent_id)` composite, owner first, because `owner_id` is in every `WHERE` clause in
the application and a composite index can serve a prefix of its columns — so one index covers
both "this owner's children of X" and "this owner's rows".

**Q18. Your text columns pin a collation. Why?**
Because index ordering and comparison follow the collation, and a database restored with a
different default can leave an index that no longer matches the comparisons made against it —
which is corruption that reads as a missing row. The pin makes the ordering a property of the
schema rather than of the server it happens to be running on. CI runs the suite against both a
C-ordered database and a `en_US.UTF-8` one, because the suite cannot otherwise tell a pinned
index from a default that happens to agree with it. → [Chapter 15](15-containers-and-ci.md)

**Q19. Where is the one place this design would struggle?**
Ancestry at scale. Every breadcrumb is a recursive walk, and if this became a permission system
doing millions of ancestry checks against rare writes, that is the wrong shape. The answer then
is to **add** a closure table maintained by trigger as a derived index, keeping the adjacency
list as the source of truth — not to replace it. I would want measurements before doing that,
because the trigger introduces a second structure that can disagree with the first.

---

## Where the rest live

| Questions | Subject | Chapter |
|---|---|---|
| Q20–Q24 | The API contract, problem documents, pagination | [Chapter 11](11-the-api-contract.md) |
| Q25–Q30 | Sessions, CSRF, isolation, rate limits | [Chapter 12](12-identity-and-isolation.md) |
| Q31–Q33 | Mutation testing, coverage, what tests cannot prove | [Chapter 14](14-testing-what-matters.md) |

And the five that are worth ten minutes rather than thirty seconds are
[Chapter 19](19-five-deep-dives.md).

---

**Next:** [Chapter 19 · Five deep dives](19-five-deep-dives.md)
