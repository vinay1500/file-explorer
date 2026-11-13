# Contributing

**File Explorer** — a multi-user hierarchical folder manager.
Monorepo: Flask API in `apps/api`, React frontend in `apps/web`.

Read this before your first change. It is short and it is binding.

Companion documents: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/SECURITY.md](docs/SECURITY.md), [docs/DESIGN-SYSTEM.md](docs/DESIGN-SYSTEM.md).

---

## 0. The rule that outranks the rest

**Never report a test as passing unless you ran it and it passed.**

The same goes for every factual claim in a commit message, a pull request or the docs. If you
did not run it, read it, or measure it, say so. "I believe" and "this should" are fine; stating
an unverified thing as fact is not. A wrong fact that survives into the README costs more than a
missing one.

---

## 1. What this project optimises for

In order:

1. **Tree invariants** — cycles, orphans, duplicate names, depth.
2. **Concurrency** — two simultaneous moves must not be able to corrupt the tree.
3. **Test evidence** — integration tests against real PostgreSQL. Not SQLite, not mocks.
4. **API contract** — consistent error shapes, documented in OpenAPI.
5. **Authorization** — a user can never read or touch another user's nodes.

A change that improves one of those is worth making. A change that does not is scope creep
unless it was asked for. Cosmetic refactors are not free — they dilute the diff a reviewer reads.

---

## 2. Before writing any code

1. Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §3 (data model) and §4 (invariants).
   They are short and binding.
2. Read the existing code in the module you are about to touch. Match its patterns.
3. Check `make help` for the available commands. Do not invent new ones silently.

---

## 3. Project rules

These are not general advice. They are this repository's rules.

### Tree operations
- **All tree mutations go through `apps/api/src/file_explorer/services/tree.py`.** No route
  handler performs a raw `db.session` write against `nodes`. Routes validate, call a service,
  and serialise the result.
- **Any change to move or delete adds a case to `apps/api/tests/test_tree_invariants.py`.**
  The invariants in ARCHITECTURE §4 are numbered — reference the number.
- **Never set `parent_id` directly**, including in fixtures and seed scripts. Fixtures that
  build invalid trees defeat the tests that depend on them.
- Recursive CTEs live in `repositories/node_repo.py`, parameterised, never f-string composed.

### API
- **No endpoint without a marshmallow schema and an integration test.** The schema generates the
  OpenAPI document; an undocumented endpoint is a broken contract.
- Every error response uses the Problem Details shape in ARCHITECTURE §6. Never return a bare
  string or a raw exception message.
- A node belonging to another user returns **404, never 403**. See SECURITY §2.1.

### Database
- **Schema changes are Alembic migrations.** `db.create_all()` must not reappear anywhere, and
  there must never be an HTTP route that performs DDL.
- Every new foreign key gets an explicit `ondelete` and an index on the referencing column.

### Frontend
- API types in `apps/web/src/api/generated/` are generated from the OpenAPI document.
  **Never hand-edit them.** If they are wrong, fix the backend schema and regenerate.
- Mutations use TanStack Query with an optimistic update and a rollback on error. A failed
  request must produce a visible error state — `console.error` alone is a bug.
- No hardcoded API hostnames. Read `VITE_API_BASE_URL` from the environment.

### Never
- Commit a virtualenv, `node_modules`, `__pycache__`, or a `.env`. Check `git status` first.
- Add a dependency without saying in the pull request what it replaces or why it is needed.
- Weaken a test to make it pass. If a test is wrong, say so and fix it deliberately.
- Leave a placeholder (`[VALUE]`, `TODO`, `FIXME`, lorem text) in anything committed.

---

## 4. Running things

| Task | Command |
|---|---|
| Full stack, local | `make up` (Compose: api, web, postgres) |
| API tests | `make test-api` |
| Web unit tests | `make test-web` |
| End-to-end | `make test-e2e` |
| Lint and format | `make lint` |
| Type check | `make typecheck` |
| Regenerate API types | `make openapi` |
| Guard mutation check | `make verify-guards` |
| Everything CI runs | `make ci` |

Tests run against real PostgreSQL. A test that passes on SQLite and fails on PostgreSQL has
proved nothing — recursive CTEs, `FOR UPDATE` and partial unique indexes all behave differently.

---

## 5. Working method

For anything beyond a one-line fix:

1. Write the failing test first. Show it failing.
2. Implement the smallest change that makes it pass.
3. Run `make ci` and report the real output.
4. Re-read the diff for anything unrelated that crept in.

Work happens on branches and merges by pull request — never a push to `main`, and never through
the GitHub web editor.

### Commit messages

Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`.

- Subject under about 60 characters, lowercase after the prefix, imperative, specific.
  `fix: lock ancestry chain before move` — not
  `fix: implement comprehensive row-level locking`.
- A body only when the change needs one, and then it explains **why**. The diff says what.
- No emoji. No `This commit...` preamble. No bulleted summary restating the diff.
- Reference an invariant or finding id where one applies:
  `fix: cascade deletes to subtree (I3, F-03)`.

The same register applies to code comments, pull request descriptions and README prose.

---

## 6. Reporting work

State what changed and which files; which commands were run and their **actual** output, not
what you expect it to be; what you could not verify; and any assumption you made.

Be concise. Say plainly when something failed.

---

## 7. Security and approvals

Follow [docs/SECURITY.md](docs/SECURITY.md). Get sign-off before:

- changing authentication, authorization or session handling
- running a migration against anything but the local Compose database
- deleting data, force-pushing, rewriting history, or changing a deployment
- adding anything that reads or writes a secret

---

## 8. Documentation ships with the code

`docs/book/` is a living reference written **alongside** the implementation, never after it.
Each milestone's pull request updates the chapters it affects, in the same pull request as the
code.

- A chapter describing behaviour that does not exist yet says so, plainly, in the text.
- When a change makes something real, the chapter moves from "will" to "does" **in that pull
  request**, and names the test that proves it. Never upgrade the tense without the test.
- Corrections are appended to the change log, never edited in place.
- Anything believed but not demonstrated stays marked unverified.

| Milestone | Chapters it must update |
|---|---|
| M0 · stop the bleeding | 7, change log |
| M1 · merge & ground truth | 16, 17, change log |
| M2 · model & invariants | 3, 4, 5, 6, 9, 10 |
| M3 · API contract | 11, Q20–Q24 |
| M4 · test gauntlet | 14, 15, Q31–Q33 |
| M5 · auth & scoping | 12, Q25–Q30 |
| M6 · frontend rebuild | 5, 13 |
| M7 · docs & demo | 7, 17, 20, change log |

A milestone is not finished while its chapters still describe the previous state.

---

## 9. Definition of done

- [ ] The behaviour requested is implemented.
- [ ] A test covers it, and the test was run and passed.
- [ ] `make ci` is green locally.
- [ ] `make verify-guards` still catches all three mutations.
- [ ] OpenAPI regenerated if any endpoint or schema changed.
- [ ] No unrelated changes, no placeholders, no secrets in the diff.
- [ ] The affected book chapters were updated in this pull request.
- [ ] Remaining risks stated explicitly.
