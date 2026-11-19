# The File Explorer Book

A long-form reference for this project: what it is, what was wrong with the first version, and
the reasoning behind every decision taken to fix it.

## How this book is maintained

**It is written alongside the code, never after it.** Each milestone's pull request updates the
chapters it affects, in the same pull request as the code that makes them true. Three rules keep
it honest:

1. **Claims move category, never quietly.** A sentence written as *will* becomes *does* only
   when a test proves it, and the test is named in the text.
2. **Corrections are appended, not edited.** When something here turns out to be wrong it gets a
   row in [CHANGELOG.md](CHANGELOG.md) rather than a silent rewrite.
3. **Unverified stays visible.** Anything believed but not demonstrated is marked, every time.

A chapter exists once it has something true to say. The table below lists all twenty and when
each is written — **a missing file means that milestone has not landed yet**, not that the
chapter was forgotten. Empty placeholder files are worse than absent ones.

Seventeen of twenty are written. A chapter exists once it has something true to say, so this
number is the count of files, not a target &mdash; and a test now asserts that this sentence and
the directory agree, because the number has been wrong twice.
Every claim in them is either forensics on code that shipped in
April 2025, or a statement about code in this repository that names the test proving it.

---

## Volume I · The Shape of Trees

Why hierarchical data is hard in a relational store, and why this schema.

| # | Chapter | Status |
|---|---|---|
| 1 | Why hierarchies are hard | M9 |
| 2 | [Four ways to store a tree](02-four-ways-to-store-a-tree.md) | **written** |
| 3 | [The schema, line by line](03-the-schema.md) | **written** |
| 4 | [The eight invariants](04-the-eight-invariants.md) | **written** |

## Volume II · What Went Wrong

Forensics on the version that shipped in April 2025.

| # | Chapter | Status |
|---|---|---|
| 5 | [The cycle](05-the-cycle.md) | **written** |
| 6 | [The orphans](06-the-orphans.md) | **written** |
| 7 | [The silent outage](07-the-silent-outage.md) | **written** |
| 8 | [The register](08-the-register.md) | **written** |

## Volume III · The Rebuild

How each failure is actually fixed, and why the obvious fix is not always enough.

| # | Chapter | Status |
|---|---|---|
| 9 | [Recursive CTEs](09-recursive-ctes.md) | **written** |
| 10 | [The race nobody sees](10-the-race-nobody-sees.md) | **written** |
| 11 | [The API contract](11-the-api-contract.md) | **written** |
| 12 | [Identity and isolation](12-identity-and-isolation.md) | **written** |
| 13 | [The client that could not say null](13-the-client-that-could-not-say-null.md) | **written** |

## Volume IV · The Practice

The machinery around the code.

| # | Chapter | Status |
|---|---|---|
| 14 | [Testing what matters](14-testing-what-matters.md) | **written** |
| 15 | [Containers and CI](15-containers-and-ci.md) | **written** |
| 16 | [Two repositories become one](16-two-repositories-become-one.md) | **written** |
| 17 | [Architecture on no budget](17-architecture-on-no-budget.md) | **written** |

## Volume V · The Defence

| # | Chapter | Status |
|---|---|---|
| 18 | Questions and answers | M9 · Q20–Q33 already live in their own chapters |
| 19 | Five deep dives | M9 |
| 20 | [The stories to tell](20-the-stories-to-tell.md) | **written** |

---

[Change log](CHANGELOG.md) · [Architecture](../ARCHITECTURE.md) · [Security](../SECURITY.md)
