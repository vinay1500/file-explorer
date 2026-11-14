# Change log

Appended, never edited. When something in this book turns out to be wrong, the original
statement stays and a new row explains what is true instead — with the date it changed.

| Date | Change | Why |
|---|---|---|
| 2025-04-18 | Project started as two repositories — Flask API and a Create React App frontend | Original build |
| 2025-04-21 | Deployed: API on Render, frontend on Vercel, PostgreSQL on Render's free tier | Original build |
| ~2025-06 | Database became unreachable; the demo silently served an empty tree | Cause established at M0; see [chapter 7](07-the-silent-outage.md) |
| 2026-03-28 | Two commits removing `__pycache__` files | Last activity before the rebuild |
| 2026-11-13 | Audit: 24 findings recorded, each tagged by how it is known. One claim in the prior review corrected | Evidence gathered before writing any code |
| 2026-11-13 | **M0 complete.** Demo link withdrawn, service suspended, public DDL endpoint removed, `.gitignore` added | Stop ongoing harm before building |
| 2026-11-13 | Governance docs restructured — `CONTRIBUTING.md` published, working notes kept local | Nothing committed should read as machine-generated |
| 2026-11-13 | **M1 graft.** Both repositories merged into one, preserving all 30 original commits. A committed virtualenv was removed from every commit, taking the repository from 12 MB to 144 KB | One repo is a precondition for integration CI and end-to-end tests |
| 2026-11-13 | Commit authorship normalised to a single identity across all 36 commits | Done before the first push, while it was still free |
| 2026-11-13 | Decision: no local runtime. Tests run in GitHub Actions against a service-container PostgreSQL | Nothing installs on the development machine |
| 2026-11-13 | Chapter 7 written | M0 made it true |
| 2026-11-14 | **M2 data model.** `users` and `nodes` with the composite owner key, both partial unique indexes and the self-parent check. Reversible migration, each constraint asserted in CI by attempting to violate it | Four of the eight invariants moved from the service layer into the database |
| 2026-11-14 | Review before writing the move algorithm found two critical faults in code already committed: `delete-orphan` would have deleted a subtree on move-to-root, and `btrim(name)` with one argument let a tab-only name through | Both fixed before the algorithm was written; see [chapter 6](06-the-orphans.md) and [chapter 3](03-the-schema.md) |
| 2026-11-14 | **M2 move algorithm.** Recursive-CTE ancestry check inside a transaction holding `FOR NO KEY UPDATE` on the moved node and the destination chain, with a deadlock retry | [Chapter 10](10-the-race-nobody-sees.md) |
| 2026-11-14 | 22 tests against real PostgreSQL. The concurrency test is a four-cycle across two trees, not the obvious two-cycle | The two-cycle passes against a broken implementation; see [chapter 10](10-the-race-nobody-sees.md) |
| 2026-11-14 | Correction: `ARCHITECTURE` §5 credited the destination chain for the concurrency guarantee. **The load-bearing lock is the one on the moved node.** | Found by review; the earlier statement was wrong, not merely imprecise |
| 2026-11-14 | Correction: `ORDER BY id` was stated as preventing deadlock. It is a convention, not a guarantee — a cascading delete locks in scan order, so `40P01` stays reachable and `move` retries | Same review |
| 2026-11-14 | Correction: seven statements in committed prose described things that did not exist — a test suite, a scheduled probe, a TypeScript frontend, a working `make db` | Audited against the repository and corrected; see the git history for 14 November |
| 2026-11-14 | Chapters 3, 4, 5, 6, 9, 10, 16 and 17 written | The code they describe now exists |
| 2026-11-14 | Review of PR #1 found that **two tests could not fail for their stated reasons** — the move-to-root test never touches the ORM, and the cross-owner path test cannot build the state it guards against | Both rewritten; the second drops the foreign key, builds the illegal state and asserts the walk stops anyway |
| 2026-11-14 | Correction: `delete-orphan` was described as deleting on any de-association. **Measured:** it fires only when the parent's collection is loaded. The first replacement test used the cold path and passed with the bug restored | [Chapter 6](06-the-orphans.md) now carries the measurement rather than the claim |
| 2026-11-14 | Both fixes verified by reintroducing the bugs on a throwaway branch and confirming CI goes red | What the guards are supposed to do, done to the guards themselves |
| 2026-11-14 | Review found the retry branch had **zero coverage** — roughly 25 lines at the centre of the concurrency design, never executed. The existing four-cycle test releases at 0.6 s against a 4 s `lock_timeout`, so it never reaches the branch | Four tests added, released by the service's own log record rather than a sleep; see [chapter 10](10-the-race-nobody-sees.md) |
| 2026-11-14 | Correction: `57014` was briefly asserted to belong in `RETRYABLE` — by a test of mine. **A statement that exceeded its budget will exceed it again**, so retrying burns the timeout twice and still fails. Mapped to 503 instead | The design was changed to the right answer rather than the test to the convenient one |
| 2026-11-14 | **Bug: migrations silenced the application's logging.** `fileConfig` defaults to `disable_existing_loggers=True`, so running Alembic set `.disabled` on every logger `alembic.ini` does not name — `file_explorer.*` and `flask.app` both | A disabled logger drops records inside `isEnabledFor`, before any handler; see [chapter 10](10-the-race-nobody-sees.md) |
| 2026-11-14 | That bug is why the two new retry tests failed. The loop was retrying correctly the whole time and the warning it logs was being swallowed | The second failing test proved it: a `55P03` escaped the loop having logged nothing, which the code cannot do |
| 2026-11-14 | The production form is worse than the test form: a process that runs migrations on startup then serves traffic emits no contention warnings and no health-check failures for its whole life | Same shape as [chapter 7](07-the-silent-outage.md) — silence reads identically to health |
| 2026-11-14 | The regression test reads `logging.Logger.manager.loggerDict` rather than calling `getLogger`, because `getLogger` creates a missing logger already enabled and would pass with the bug restored | Verified both ways before pushing |
