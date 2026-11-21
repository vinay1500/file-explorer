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

A chapter exists once it has something true to say. Empty placeholder files are worse than
absent ones, so for most of this project's life the table below had gaps, and each gap meant
that milestone had not landed yet.

Twenty-two of twenty-two are written, and the number is a count of the files rather than a
target — a test asserts that this sentence and the directory agree, that every chapter is linked
from here, **that every chapter link points at its own chapter, and that the forward chain has no
gap in it.** The count had been wrong twice. The links were worse: five of them pointed back at
this index, each one written while the next chapter did not exist yet and never updated when it
arrived, and chapter 6 skipped straight over 7 and 8 — so a reader following the book end to end
never saw the outage or the register. The index test could not see any of that, because it only
ever asked whether this page links to the chapters.

Every claim in them is either forensics on code that shipped in April 2025, or a statement about
code in this repository that names the test proving it.

---

## Volume I · The Shape of Trees

Why hierarchical data is hard in a relational store, and why this schema.

| # | Chapter | Status |
|---|---|---|
| 1 | [Why hierarchies are hard](01-why-hierarchies-are-hard.md) | **written** |
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
| 18 | [Questions and answers](18-questions-and-answers.md) | **written** · Q1–Q19; the rest live in their own chapters |
| 19 | [Six deep dives](19-five-deep-dives.md) | **written** |
| 20 | [The stories to tell](20-the-stories-to-tell.md) | **written** |

## Volume VI · The Tour

Where to start reading the code itself. Both chapters follow the same journey — one drag, from
the pixel to the row and back — once as a map and once as a story.

| # | Chapter | Status |
|---|---|---|
| 21 | [Reading the code, in the order it runs](21-reading-the-code.md) | **written** · eleven stops, each with the line to put your cursor on |
| 22 | [The same tour, as a story](22-the-same-tour-as-a-story.md) | **written** · the same walk in prose, no line numbers |

---

[Change log](CHANGELOG.md) · [Architecture](../ARCHITECTURE.md) · [Security](../SECURITY.md)
