# 0005 — One repository

Accepted · 2026-11-14

## Context
The project began as two repositories, `file-explorer-backend` and `file-explorer-frontend`.

## Decision
One repository with `apps/api` and `apps/web`, built by stripping a committed virtualenv out
of the backend's history with `git filter-repo`, then grafting both with `git subtree add`
**without** `--squash`.

## Consequences
An integration test against a real database, a CI pipeline that stands the system up, and a
browser test driving the UI against a live backend each need one checkout, one compose file
and one workflow. Across two repositories none of them has a home — so this is a
precondition, not a preference.

All 30 original commits survive, dated from April 2025. History rewriting changed every SHA;
authors and dates did not. Repository size went from 12 MB to 144 KB.

## Alternatives
- **Two repositories, cross-linked.** An hour's work, fixes nothing that matters.
- **Monorepo with submodules.** Reintroduces every problem above and adds submodule handling
  in CI, for no gain at this size.
- **Fresh history.** Rejected: a repository whose entire history is sixty immaculate commits
  across ten days reads as staged, and deleting history to look more competent is the
  opposite of the evidence discipline applied everywhere else here.
