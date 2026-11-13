# File Explorer

A multi-user hierarchical folder manager — create, rename, move and delete folders and files in
a tree of arbitrary depth, through a drag-and-drop interface backed by a REST API over
PostgreSQL. No file contents are stored, only structure.

> **Status: rebuild in progress.**
> This repository began in April 2025 as two separate repos. It is being rebuilt in the open —
> the commit history below that point is the original. What is documented in
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) is the **target**, and that document marks what
> exists today versus what is planned. Nothing here claims to be finished.

---

## Why this exists

The original version worked, and had a bug worth understanding: moving a folder into its own
descendant created a cycle. No error was raised and no row was corrupted — but the subtree
stopped being reachable from the root, so to the user the data had vanished.

Fixing that properly is more interesting than it first appears. A descendant check is the
obvious answer, and it is not sufficient: two concurrent moves can each pass that check against
a tree that is still valid at the moment they look, and commit a cycle between them. The real
fix runs the check inside a transaction that holds row locks on the destination's ancestry
chain, in a deterministic order.

That problem — and the tests that prove it is solved — is what this rebuild is about.

---

## Architecture

```mermaid
flowchart TD
    W["apps/web — React + TypeScript<br/>drag-and-drop tree"]
    B["Blueprints — routing and serialisation"]
    S["Services — business rules:<br/>cycles · depth · ownership"]
    R["Repositories — SQL,<br/>including recursive CTEs"]
    D[("PostgreSQL<br/>cascades · unique indexes · checks")]

    W -->|"fetch, cookie auth,<br/>types generated from OpenAPI"| B
    B --> S
    S --> R
    R --> D
```

Rules that must never be violated live in the database as constraints. Rules that need a good
error message live in the service layer. Most live in both.

### The data model

An **adjacency list** — each node stores its parent. Chosen because the defining operation here
is moving a subtree, and that is a single-row update in an adjacency list, where a nested set
would renumber much of the table and a materialised path would rewrite every descendant.

```
nodes
  id         bigserial  primary key
  owner_id   bigint     → users(id)  on delete cascade
  parent_id  bigint     → nodes(id)  on delete cascade   -- NULL means root
  name       varchar(255)
  type       enum('file','folder')

  index  (owner_id, parent_id)
  unique (parent_id, name) where parent_id is not null
  unique (owner_id, name)  where parent_id is null
  check  (parent_id is distinct from id)
```

Two partial unique indexes rather than one constraint, because PostgreSQL treats NULLs as
distinct for uniqueness — a plain `unique (parent_id, name)` would permit unlimited duplicate
names at the root.

The eight invariants this schema and its service layer maintain are listed in
[`docs/ARCHITECTURE.md` §4](docs/ARCHITECTURE.md), each with the test that proves it.

---

## Layout

```
apps/api     Flask API — PostgreSQL, SQLAlchemy, marshmallow
apps/web     React frontend
docs/        architecture, security, design system
```

Both application directories carry their original history from the two repositories this
replaces, with a committed virtualenv removed from every commit.

---

## Running it

```bash
git clone https://github.com/vinay1500/file-explorer.git
cd file-explorer
make help      # every available command
```

**What works today:** `make db` starts PostgreSQL 17 in a container.

**What does not work yet:** `make up`, `make test-api` and the rest. The application images and
the test suite are being written; those targets fail with a pointer rather than a confusing
build error. This section gets updated as each one starts working — see the
[change log](docs/book/) for what landed when.

When the test suite exists it will run against PostgreSQL, never SQLite. Recursive CTEs,
`SELECT … FOR UPDATE` and partial unique indexes all behave differently there, so a suite that
passes on SQLite has tested a different program.

---

## Documentation

| | |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Data model, invariants, the move algorithm, API conventions |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat surface and the tests that enforce it |
| [docs/DESIGN-SYSTEM.md](docs/DESIGN-SYSTEM.md) | Tokens, component states, accessibility |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Project rules, commit conventions, definition of done |
