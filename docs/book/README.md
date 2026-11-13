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

---

## Volume I · The Shape of Trees

Why hierarchical data is hard in a relational store, and why this schema.

| # | Chapter | Status |
|---|---|---|
| 1 | Why hierarchies are hard | M2 |
| 2 | Four ways to store a tree | M2 |
| 3 | The schema, line by line | M2 |
| 4 | The eight invariants | M2 |

## Volume II · What Went Wrong

Forensics on the version that shipped in April 2025.

| # | Chapter | Status |
|---|---|---|
| 5 | The cycle | M2 |
| 6 | The orphans | M2 |
| 7 | [The silent outage](07-the-silent-outage.md) | **written** |
| 8 | The register | M2 |

## Volume III · The Rebuild

How each failure is actually fixed, and why the obvious fix is not always enough.

| # | Chapter | Status |
|---|---|---|
| 9 | Recursive CTEs | M2 |
| 10 | The race nobody sees | M2 |
| 11 | The API contract | M3 |
| 12 | Identity and isolation | M5 |
| 13 | Making it fast | M6 |

## Volume IV · The Practice

The machinery around the code.

| # | Chapter | Status |
|---|---|---|
| 14 | Testing what matters | M4 |
| 15 | Containers and CI | M4 |
| 16 | Two repositories become one | M1 — next |
| 17 | Architecture on no budget | M1 — next |

## Volume V · The Defence

| # | Chapter | Status |
|---|---|---|
| 18 | Questions and answers | M3–M5, alongside the features they cover |
| 19 | Five deep dives | M5 |
| 20 | The stories to tell | M7 |

---

[Change log](CHANGELOG.md) · [Architecture](../ARCHITECTURE.md) · [Security](../SECURITY.md)
