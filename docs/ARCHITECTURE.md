# Architecture

> **Project:** File Explorer · **Status:** PROPOSED — rebuild not yet started · **Updated:** 2026-11-13

**Nothing in this document is built yet.** It describes the target. Items marked ✅ exist in the
current repositories; everything else is planned. The current state is recorded separately in
the ground-truth file.

---

## 1. Purpose

**What it does:** a multi-user file and folder manager. Users create, rename, move and delete
folders and files in an arbitrarily deep hierarchy, through a drag-and-drop tree in the browser,
backed by a REST API over PostgreSQL. No file contents are stored — only the structure.

**Primary users:** authenticated individuals, each with a private tree. There is no sharing,
no collaboration and no public access in scope.

**Core capabilities:**
- Create, rename, move and delete nodes in a hierarchy of unbounded width and bounded depth ✅ (partially)
- Drag a folder into another folder, with cycles rejected by the server
- Breadcrumb navigation computed server-side from an ancestry query
- Per-user isolation: a user can never observe that another user's node exists

**Explicitly out of scope:** file upload and storage, sharing and permissions, versioning,
trash/restore, search, real-time collaboration.

---

## 2. Technology stack

| Layer | Now | Target | Note |
|---|---|---|---|
| Web framework | React 19.1.0 ✅ | React 19 | Kept |
| Web build | Create React App / react-scripts 5.0.1 ✅ | **Vite 7** | CRA is deprecated |
| Web language | JavaScript ✅ | **TypeScript 5** | API types generated from OpenAPI |
| Server state | React Context ✅ | **TanStack Query v5** | Optimistic updates with rollback |
| HTTP client | axios 1.8.4 ✅ | fetch wrapper, typed | Drops a dependency |
| Tree / drag-drop | react-dnd 16 + @minoru/react-dnd-treeview 3.5 ✅ | Same | Works; `canDrop` to be fixed |
| Styling | Inline style objects + two CSS files ✅ | CSS custom properties + CSS Modules | See DESIGN-SYSTEM.md |
| Web tests | one failing CRA default ✅ | **Vitest + Testing Library + Playwright** | |
| API language | Python 3.12 ✅ | Python 3.12 | Kept |
| API framework | Flask 3.1.0 ✅ | Flask 3.1 + **flask-smorest** | Decision D-03: stay on Flask |
| Validation | none ✅ | **marshmallow 3** via flask-smorest | Also generates OpenAPI |
| ORM | Flask-SQLAlchemy 3.1.1 / SQLAlchemy 2.0.40 ✅ | Same | |
| Migrations | none — `db.create_all()` ✅ | **Alembic** via Flask-Migrate | |
| Auth | none ✅ | **Flask-JWT-Extended** in cookie mode | Decision D-05 |
| Password hashing | none ✅ | **argon2-cffi** | |
| Rate limiting | none ✅ | **Flask-Limiter** | Auth endpoints only |
| Logging | Flask default ✅ | **structlog**, JSON, request-id bound | |
| DB driver | psycopg2-binary 2.9.10 ✅ | **psycopg 3** | What a bare `postgresql://` resolves to in SQLAlchemy 2.x; `PyMySQL` removed |
| Database | PostgreSQL ✅ | PostgreSQL 17 | Recursive CTEs, partial unique indexes |
| WSGI | gunicorn 23.0.0 ✅ | gunicorn | |
| API tests | none ✅ | **pytest** + pytest-cov against real PostgreSQL | |
| Lint / format | eslint-config-react-app ✅ | **Ruff** (api), **ESLint 9 + Prettier** (web) | |
| Type check | none ✅ | **mypy** (api), **tsc** (web) | |
| Local dev | none ✅ | **Docker Compose** | api + web + postgres |
| CI | none ✅ | **GitHub Actions** | |
| API docs | none ✅ | **OpenAPI 3.1** + Swagger UI | Generated, not hand-written |
| Diagrams | none ✅ | **Mermaid** in Markdown | Renders natively on GitHub |

Core technologies are not to be replaced without an ADR.

### Hosting

| Concern | Now | Target | Why |
|---|---|---|---|
| Web | Vercel ✅ | Vercel Hobby | Free, fine as-is |
| API | Render free web service ✅ | Render free web service | Free; spins down after 15 min idle |
| Database | Render free PostgreSQL ✅ **(expired and deleted)** | **Neon free** | Render free Postgres is deleted 30 days after creation; Neon's free tier has no expiry |
| Keep-warm | none | GitHub Actions cron | Mitigates the ~52 s cold start during waking hours |
| Deploy config | **none in either repo** ✅ | `render.yaml` + `vercel.json` committed | Today the deployment exists only as dashboard settings and cannot be recreated from source |

---

## 3. Data model

Two tables. The hierarchy is an **adjacency list** — each node stores its parent. This is the
correct choice at this scale and is kept from the original design; see `docs/adr/0001`.

```sql
CREATE TYPE node_type AS ENUM ('file', 'folder');

CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    email         CITEXT      NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE nodes (
    id         BIGSERIAL PRIMARY KEY,
    owner_id   BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parent_id  BIGINT       NULL     REFERENCES nodes(id) ON DELETE CASCADE,
    name       VARCHAR(255) NOT NULL,
    type       node_type    NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT ck_nodes_not_own_parent CHECK (parent_id IS DISTINCT FROM id),
    CONSTRAINT ck_nodes_name_nonempty  CHECK (length(btrim(name)) > 0)
);

-- the index that makes every child lookup and every CTE step cheap
CREATE INDEX idx_nodes_owner_parent ON nodes (owner_id, parent_id);

-- sibling names unique within a folder …
CREATE UNIQUE INDEX uq_nodes_parent_name
    ON nodes (parent_id, name) WHERE parent_id IS NOT NULL;

-- … and unique at the root, per owner (NULL parent_id needs its own partial index)
CREATE UNIQUE INDEX uq_nodes_root_name
    ON nodes (owner_id, name) WHERE parent_id IS NULL;
```

**Why two partial unique indexes rather than one constraint:** in PostgreSQL, `NULL` values are
distinct for uniqueness purposes, so a plain `UNIQUE (parent_id, name)` would allow unlimited
duplicate names at the root. `NULLS NOT DISTINCT` (PostgreSQL 15+) would also work; partial
indexes are chosen because they additionally scope root-level uniqueness by owner.

**What changes from the current schema:** `ON DELETE CASCADE` on `parent_id` (currently absent,
which is why deleting a folder scatters its children to the root), the index on the parent
column, both unique indexes, the two check constraints, `owner_id` entirely, and timestamps.

---

## 4. Invariants

These are the contract. Each is numbered, each has a test, and the test names the number.

| # | Invariant | Enforced by | Test |
|---|---|---|---|
| **I1** | A node is never its own ancestor — no cycles | Recursive CTE check inside a row-locked transaction | `test_move_into_descendant_rejected` |
| **I2** | `parent_id IS NULL` means root; roots are scoped per owner | `uq_nodes_root_name` | `test_root_names_unique_per_owner` |
| **I3** | Deleting a node deletes its entire subtree — no orphans | `ON DELETE CASCADE` + `cascade="all, delete-orphan"` | `test_delete_cascades_whole_subtree` |
| **I4** | Sibling names are unique within a parent | `uq_nodes_parent_name` | `test_duplicate_sibling_name_rejected` |
| **I5** | A file is always a leaf | Service layer rejects a move into a `file` | `test_cannot_move_into_a_file` |
| **I6** | Every node in a subtree shares one `owner_id` | Service layer; move validates both ends | `test_cannot_move_across_owners` |
| **I7** | Depth never exceeds 64 levels | Depth check in the move/create service | `test_depth_cap_enforced` |
| **I8** | A node's `id` never equals its `parent_id` | `ck_nodes_not_own_parent` | `test_self_parent_rejected_at_db_layer` |

I8 is enforced at the database layer deliberately: it is the one case the current code already
checks in Python, and pushing it into a constraint means no future code path can bypass it.

---

## 5. The move operation

The centre of the project. A move must reject cycles **and** remain correct when two moves run
concurrently.

```sql
BEGIN;  -- READ COMMITTED

-- 1. Lock the node being moved together with the destination's entire ancestry chain.
--    ORDER BY id gives every transaction the same lock order, which prevents deadlock
--    between two opposing moves.
WITH RECURSIVE ancestry AS (
        SELECT id, parent_id FROM nodes WHERE id = :new_parent_id
    UNION ALL
        SELECT n.id, n.parent_id
          FROM nodes n JOIN ancestry a ON n.id = a.parent_id
)
SELECT id
  FROM nodes
 WHERE id IN (SELECT id FROM ancestry) OR id = :node_id
 ORDER BY id
   FOR UPDATE;

-- 2. With the chain locked, re-run the ancestry walk. If :node_id appears in the
--    destination's ancestry, the move would create a cycle -> 409 CYCLE_DETECTED.
-- 3. Otherwise: UPDATE nodes SET parent_id = :new_parent_id, updated_at = now()
--               WHERE id = :node_id;

COMMIT;
```

**Why the lock is necessary.** Without it, two transactions can each read a tree in which their
move is legal, and both commit — move A into B while moving B into A. Each check passed; the
result is a cycle. Checking inside a transaction that holds the ancestry chain forces the second
transaction to re-read after the first commits.

**Why `ORDER BY id`.** Two transactions locking overlapping ancestry chains in different orders
deadlock. A deterministic order makes one of them wait instead.

`test_concurrent_move_race` drives two real connections against the compose database and asserts
exactly one succeeds and the tree is still acyclic.

---

## 6. API conventions

- **Base path and version:** `/api/v1`. Breaking changes bump the version.
- **Authentication:** JWT in an httpOnly, Secure, SameSite=Lax cookie, with a double-submit CSRF
  token required on every mutating request. See `docs/adr/0004`.
- **Error format:** RFC 9457 Problem Details, the same shape for every failure:

```json
{
  "type":     "https://github.com/vinay1500/file-explorer/docs/errors/cycle-detected",
  "title":    "Cycle detected",
  "status":   409,
  "detail":   "Cannot move node 42 into node 87, which is one of its descendants.",
  "instance": "/api/v1/nodes/42/move",
  "code":     "CYCLE_DETECTED"
}
```

- **Status codes:** `400` malformed body · `401` not authenticated · `404` not found *or not
  yours* · `409` invariant violation (cycle, duplicate name, file-as-parent) · `422` validation
  failure · `429` rate limited. Stack traces are never returned.
- **Pagination:** cursor-based on every list endpoint. No endpoint returns an unbounded set.
- **Documentation:** OpenAPI 3.1, generated from the marshmallow schemas, served at
  `/api/v1/openapi.json` with Swagger UI at `/api/v1/docs`. Hand-written API docs are not kept.

### Endpoints

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/auth/register` | Rate limited |
| `POST` | `/api/v1/auth/login` | Rate limited; sets the cookie |
| `POST` | `/api/v1/auth/logout` | Clears the cookie |
| `GET` | `/api/v1/auth/me` | Current user |
| `GET` | `/api/v1/nodes?parent_id=&cursor=&limit=` | One level, paginated |
| `POST` | `/api/v1/nodes` | Create |
| `GET` | `/api/v1/nodes/{id}` | Single node |
| `PATCH` | `/api/v1/nodes/{id}` | Rename only |
| `DELETE` | `/api/v1/nodes/{id}` | Cascades to the subtree |
| `POST` | `/api/v1/nodes/{id}/move` | `{ "new_parent_id": int \| null }` |
| `GET` | `/api/v1/nodes/{id}/subtree?depth=` | Server-side traversal, depth-capped |
| `GET` | `/api/v1/nodes/{id}/path` | Breadcrumbs, single ancestry query |

Move is a `POST` to a sub-resource rather than a field on `PATCH`, because it is an operation
with its own preconditions and its own failure mode, not a property assignment.

`new_parent_id: null` moves a node to the root. This is a legal destination and is the fix for
the current frontend bug where items can never be dragged back out of a folder.

---

## 7. Repository structure

```text
file-explorer/
├── .github/workflows/
│   ├── ci.yml                      # lint, typecheck, pytest, vitest, playwright
│   └── keep-warm.yml               # cron ping so the demo is awake in waking hours
├── apps/
│   ├── api/
│   │   ├── src/file_explorer/
│   │   │   ├── app.py              # application factory
│   │   │   ├── config.py           # env-driven settings
│   │   │   ├── extensions.py       # db, migrate, jwt, limiter
│   │   │   ├── errors.py           # Problem Details handlers
│   │   │   ├── models/             # SQLAlchemy models only
│   │   │   ├── schemas/            # marshmallow; source of the OpenAPI document
│   │   │   ├── services/           # business rules — tree.py holds the move algorithm
│   │   │   ├── repositories/       # SQL, including the recursive CTEs
│   │   │   └── blueprints/         # HTTP routing only
│   │   ├── migrations/             # Alembic
│   │   ├── tests/
│   │   │   ├── test_tree_invariants.py
│   │   │   ├── test_concurrency.py
│   │   │   ├── test_api_contract.py
│   │   │   └── test_security.py
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   └── .env.example
│   └── web/
│       ├── src/
│       │   ├── api/generated/      # from OpenAPI — never hand-edited
│       │   ├── components/         # presentational, reusable
│       │   ├── features/explorer/  # the tree feature
│       │   ├── hooks/
│       │   ├── styles/tokens.css   # the design tokens
│       │   └── types/
│       ├── e2e/                    # Playwright
│       ├── vite.config.ts
│       └── .env.example
├── docs/
│   ├── book/                       # the living reference, updated each milestone
│   ├── ARCHITECTURE.md             # this file
│   ├── SECURITY.md
│   ├── DESIGN-SYSTEM.md
│   ├── adr/0001 … 0005
│   └── diagrams/
├── CONTRIBUTING.md                 # project rules, commit conventions, done
├── docker-compose.yml
├── Makefile
├── README.md
├── LICENSE
└── .gitignore
```

---

## 8. Boundaries and data flow

```text
  Browser (React + TanStack Query)
        │  fetch, cookie-authenticated, typed from OpenAPI
        ▼
  Blueprint  ── validates with a marshmallow schema, returns Problem Details on failure
        │
        ▼
  Service    ── owns every business rule: cycles, depth, ownership, file-as-leaf
        │
        ▼
  Repository ── owns SQL, including the recursive CTEs and FOR UPDATE
        │
        ▼
  PostgreSQL ── owns what must never be violated: cascades, unique indexes, check constraints
```

- Components render and collect input; they hold no business rules.
- Blueprints do routing and serialisation only. A rule in a blueprint is a bug.
- **A rule that can be expressed as a database constraint is expressed as one.** Application
  checks produce good error messages; constraints make violations impossible.
- Source of truth: `users` for identity, `nodes` for structure. Nothing is cached server-side.

---

## 9. Quality attributes

- **Reliability:** the eight invariants hold under concurrent writes. This is the project's
  single most important property and is what the test suite exists to demonstrate.
- **Performance:** every query uses `idx_nodes_owner_parent`. Ancestry is one recursive CTE, not
  N round trips. List endpoints are paginated; subtree reads are depth-capped. Target: p95 under
  150 ms for a tree of 10,000 nodes, measured locally, excluding the free-tier cold start.
- **Security and privacy:** see `docs/SECURITY.md`. Per-user isolation is tested, not assumed.
- **Accessibility and UI consistency:** see `docs/DESIGN-SYSTEM.md`.
- **Scale:** single instance, one database, no cache. Designed for correctness at small scale,
  not for throughput. If the adjacency list ever becomes the bottleneck, `docs/adr/0001` records
  the alternatives considered and why they were rejected.
- **Observability:** structured JSON logs with a request id on every line; one log line per
  rejected invariant, including which invariant and which node ids.

---

## 10. Architecture decisions

Recorded as numbered ADRs in `docs/adr/`, not in this file, so each carries its own date,
context and consequences.

| ADR | Decision |
|---|---|
| 0001 | Adjacency list over nested sets, closure table or materialised path |
| 0002 | Recursive CTE ancestry check for cycle prevention |
| 0003 | Row-locked transaction with deterministic lock order for concurrent moves |
| 0004 | JWT in an httpOnly cookie with CSRF, over localStorage |
| 0005 | Monorepo over two repositories |

---

## 11. Change process

1. Identify the affected module and whether any invariant in §4 is touched.
2. Write the failing test first.
3. If an invariant changes, update §4 and the ADR in the same pull request.
4. Run `make ci`.
5. Breaking API changes bump the version path and are called out in the pull request.

---

## 12. Open questions

- Does the `@minoru/react-dnd-treeview` built-in descendant check survive a custom `canDrop`?
  Needs a runtime test before the frontend guard is written. Until answered, the server check
  is the only one assumed to exist.
- Soft delete with a restore window, or hard delete? Currently hard delete. Soft delete would
  complicate every uniqueness constraint and is out of scope unless a reason appears.
- Is 64 a sensible depth cap? Chosen to be far above real use and far below any recursion limit.
  No evidence either way yet.
