# Chapter 20 · The stories to tell

> **Volume V · The defence.** The last chapter. Nineteen chapters explain what was built and
> why; this one is about saying it out loud, in a room, to somebody deciding whether to hire
> you.

An interviewer is not going to read this book. They will give the repository five minutes, open
two files, and ask three questions. This chapter is the five answers worth having ready, and —
more usefully — the four things **not** to claim.

---

## 1. "Tell me about a bug you found"

The strongest version of this story is not the cycle. It is **the test that could not fail**.

> *"The milestone whose entire subject was test quality failed its own review gate. Fifteen
> tests in this repository could not fail. One asserted twelve requests against a limit of a
> hundred and twenty. One used a cursor input containing neither of the two characters its test
> was named for. One ended `or alice == 1`, which is true whenever the test user is the first
> row in the table."*

Why this one: anybody can find a bug in someone else's code. Finding fifteen in your own, on
the milestone you wrote to prove your testing was good, and then **writing it down**, is a
different claim. It says the process works on you.

The follow-up they will ask is *"how do you know there aren't more?"* The honest answer is that
you do not, and that the practice is to revert each guard on a throwaway branch and watch CI go
red — twenty-one of those runs are recorded in `docs/MUTATIONS.md` with the run id that caught
each. And that two of them **failed to apply**: a mutation that silently does nothing looks
exactly like a guard that works, which is the same failure one level up.

---

## 2. "Walk me through the hardest technical problem"

The move algorithm, and the specific thing to lead with is that **the obvious fix is not
enough**.

> *"Moving a folder into its own descendant creates a cycle. The obvious answer is a descendant
> check. That is not sufficient: two concurrent moves can each pass that check against a tree
> that is valid at the moment they look, and commit a cycle between them. The real fix runs the
> check inside a transaction that row-locks the node being moved together with the
> destination's ancestry chain, in a deterministic order — and the lock that does the work is
> the one on the moved node, not the chain."*

Then the test, because the test is the better half of the story:

> *"A two-node race often serialises by luck and passes against a broken implementation. The
> test uses four nodes across two trees, moved in a ring."*

**Do not tell this story until you can draw it.** [Chapter 10](10-the-race-nobody-sees.md) has
the sequence; the seven self-test questions are the standard. If you cannot answer them without
the page in front of you, tell story 1 instead — it is nearly as good and it cannot be probed
into collapse.

---

## 3. "Why is this interesting? It's a CRUD app"

Agree with them first, then redirect to the schema.

> *"It is a CRUD app. What makes it a systems problem is that the data is a tree in a
> relational store, so every operation is either a recursive CTE or a constraint. The one I
> would point at is the composite foreign key."*

> *"A plain `parent_id → id` foreign key constrains the id and nothing else, so a node can be
> attached under a parent belonging to a different user — and that user deleting the folder
> cascades into it. Referencing the `(id, owner_id)` pair makes a cross-owner parent a foreign
> key violation whatever the application layer forgets."*

This is the highest-value thirty seconds in the whole project. It is one line of DDL, it is
unarguable, and it demonstrates the habit that actually separates levels: **putting the
invariant where it cannot be bypassed** rather than where it is convenient.

---

## 4. "What would you do differently?"

Do not say "better planning". Say the sequencing, because it is specific and it was corrected.

> *"The frontend milestone was scheduled against a deployment that did not exist. Every choice
> about cold starts, the proxy and session cookies would have been made blind and checked on
> the last day. A review found it and we deployed first — and deploying immediately produced
> three facts no amount of reading would have: a paid-only field that would have had the
> platform reject the whole configuration file, HTTP logs behind a paywall that made a
> security setting unmeasurable, and a hostname that was not the one every file assumed."*

The general form, which is the part they are listening for: **work that depends on an
environment should be scheduled after that environment exists, not before.**

---

## 5. "How do you work with AI tools?"

They will ask. The useful answer is not about the tool.

> *"Everything is checked rather than reviewed. Documents name the tests that prove them, and a
> test fails if a document names one that does not exist. A test that is supposed to guard
> something gets reverted on a throwaway branch and watched go red. The project's own status
> page is verified against the repository by a script before it is published — its first run
> found four files the page named that no longer existed."*

Then the honest half, which is what makes it credible:

> *"That machinery exists because reviewing did not work. Fifteen tests that could not fail,
> three ticked checklist boxes naming tests that were never written, and two committed
> documents telling opposite stories about the same library for five days — all of it read
> carefully by somebody, repeatedly, and none of it caught until something could fail."*

---

## The four things not to claim

Each of these was in a document in this repository, as a flat statement, and each one was
false. They are listed so the instinct to repeat them is noticed rather than acted on.

| Do not say | Because |
|---|---|
| "Structured logging with structlog" | It is not a dependency and is implemented nowhere. It sat in `ARCHITECTURE.md` for five days while another document listed it as *not* claimable |
| "WCAG 2.2 AA, verified by axe-core in CI" | There is no axe-core. `make a11y` exists, has never been able to run, and there is **no keyboard path for moving a node** — a real AA failure, now written down as one |
| "A live demo" (until you have opened it cold) | That sentence was on a résumé for seventeen months while the demo served an empty tree |
| "Optimistic updates with rollback" | Cut at D-23. Its failure mode is showing a tree the server does not have, which is the bug this project is named about |

---

## The one-line version

If there is time for a single sentence:

> *"I took a portfolio project that had been quietly broken for seventeen months, found
> twenty-four defects in it including one in a drag-and-drop library's configuration that had
> been silently permitting the exact corruption the project is named after, and rebuilt it so
> that every claim in the documentation names the test that proves it."*

Everything in that sentence is checkable in under five minutes, which is the only property that
matters.

---

[← Chapter 19 · Five deep dives](README.md) · [Back to the book](README.md)
