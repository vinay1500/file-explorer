# Chapter 14 · Testing what matters

> **Volume IV · Proof.** Not how many tests there are. Whether any of them would
> notice.

This project has 201 tests and that number is almost meaningless. What is not
meaningless: **five of them, at different times, passed against the exact bug
they were written to catch.** Each one read correctly. Each one was reviewed.
Each proved nothing.

This chapter is about how they were found, and what they have in common.

---

## The five

| # | Test | What it asserted instead | Found by |
|---|---|---|---|
| 1 | `test_move_to_root_does_not_delete_subtree` | Nothing — the service writes raw SQL, so no ORM object is ever constructed | Gate 3 on M2 |
| 2 | `test_path_does_not_cross_owner` | A state it never built. The composite key makes it unreachable, so the walk had nothing to walk into | Gate 3 on M2 |
| 3 | `test_a_create_cannot_slip_into_a_subtree_being_deleted` | That PostgreSQL's row locks work | Mutation |
| 4 | `test_statement_timeout_is_recognised` | Three module constants, compared to themselves | Gate 3 on M3a |
| 5 | `test_the_documented_error_shape_is_the_one_actually_sent` | The media type, by **indexing** it as a literal dict key | Gate 3 on M3b |

They are not five different mistakes. They are one mistake, five times:

> **The test reaches past the thing it is testing**, and asserts something
> about a lower layer that was never in doubt.

A locking primitive works. PostgreSQL raises `57014` when a statement exceeds
its timeout. A dict has the key you just indexed. None of that is what any of
these tests were named for.

### The question that prevents it

Not *"is this assertion true when the code is right?"* — all five were.

**"What differs when it is wrong?"**

For number 3, both orderings block and both end with an empty table. Blocking
is not an observable. The only thing that separates them is the number `delete`
returns: lock-then-count says 3, count-then-lock says 2 and destroys 3 anyway.
Once that is the assertion, the test fails with
`delete reported 2 nodes but removed 3` — the number its own docstring
predicts.

For number 5 the trap was sharper. The test *derived* nothing; it indexed by
the answer it expected:

```python
ref = default["content"]["application/json"]["schema"]["$ref"]
```

The media-type half it was named for was never asserted. And fixing the
document would have made this raise `KeyError`, which reads as "the fix broke
a test" rather than "the test encoded the bug". **A test can be worse than
absent.**

---

## Mutation: the only thing that reliably catches them

The rule in this repository is that a new test is not finished until the
behaviour it covers has been reverted and CI has gone red on it.

```
1. On a throwaway branch, revert exactly one behaviour.
2. Push, open a draft pull request (CI runs on main and PRs, not every branch).
3. Read which tests went red. Exactly the ones that describe that behaviour.
4. Close the PR, delete the branch.
```

Seventeen runs are recorded in [`docs/MUTATIONS.md`](../MUTATIONS.md) with run
ids. Three are worth reading in full.

### Run 8 — the suite stayed green

Removing `delete`'s lock entirely left **every test passing**. The test that
was supposed to cover it called the locking primitive directly and never went
through `delete` at all. Nothing but mutation would have found that.

### Run 10 — the environment hid the bug class

Keyset pagination depends on the stored order and the comparison agreeing, so
the index collation is load-bearing. The obvious test inserts names and asserts
byte order.

**It proves nothing here.** This suite's PostgreSQL is initialised
`--locale=C`, so the database default already *is* byte order and that test
passes whether or not anything was ever pinned. Production is not initialised
that way — which is precisely where the bug would live.

Confirmed by mutation: with the pin removed, the ordering test still passed and
only the test reading `pg_index` failed.

> A test environment configured for convenience can hide the exact class of bug
> you are testing for. `--locale=C` makes this suite deterministic, which is
> good, and makes one whole category of collation bug invisible, which is not.

### Run 17 — why coverage is a gate

Deleting the three health-check tests left the suite **entirely green**. Only
the coverage floor noticed, at 97%.

That is the same health endpoint whose predecessor returned a constant and
stayed green for seventeen months while the database was gone
([chapter 7](07-the-silent-outage.md)). Without a floor it would have gone
quietly back to being untested.

---

## What mutation cannot do

It is not a substitute for review, and M3a proved it.

Every behaviour in that milestone was mutation-checked. Three rounds. Gate 3
still found a blocking defect: `create` locked a chain chosen under one
snapshot and measured another, so a move committing during the lock wait left
the upper chain unprotected and a second, individually legal move pushed a node
past the depth cap.

Every individual guard fired when mutated. They were simply guarding a chain
that had moved.

> **Mutation proves a guard bites. It cannot prove a guard is in the right
> place.** That is what fresh eyes are for, and it is why Gate 3 reads the
> finished diff with no knowledge of the plan — a plan-aware reviewer
> rationalises.

---

## The test that aimed at an element

There is a worse failure than a test that cannot fail, and this project shipped
it.

`drag-to-root.spec.ts` drives a real browser. It opens the app, expands a
folder, drags a nested folder out to the root, and asserts the **request body**
is `{"new_parent_id": null}` — not the redraw, because a redraw assertion
passes against a client that sends `0`, gets a 404 and re-reads an unchanged
tree. It is a careful test. It was green on every run from the day it was
written.

The feature it covers was impossible.

The drag library signals *the root* by leaving `dropTargetNode` undefined,
which happens when the pointer is over the tree's own `<ul>` rather than over
one of its rows. That `<ul>` was exactly as tall as the sum of its rows. There
was no part of it a pointer could be over. Measured in the deployed page:

```
rootBox.h        151.1875
rowsTotalHeight  151.1875
elementFromPoint → .tree-root : 0 of 1,368 sampled points
```

The test passed because it never asked that question. It calls
`dispatchEvent` on a locator, and `dispatchEvent` delivers the event to the
element you name. Hit testing — the part of the browser that decides what is
under a coordinate — is not involved at all.

> **A test aims at an element. A person aims at a point.**

Everything between those two sentences is where this defect lived for four
days, through a review gate that ran the browser suite ten times, counted its
flake rate, and downloaded the library to check a comment.

### What the replacement does differently

It never names the target:

```ts
for (let y = lowestRow + 2; y < box.bottom - 2; y += 4) {
  for (let x = box.left + 4; x < box.right - 4; x += 24) {
    if (document.elementFromPoint(x, y) === root) hits += 1;
  }
}
expect(hits).toBeGreaterThan(20);
```

It asks the document what is under a coordinate, and the drag that follows is
aimed at a point the probe returned rather than at a selector. If the band ever
loses its area again — a padding value changed, an overlay moved — the probe
returns zero and the suite goes red for the reason a person would have hit.

### The rule this leaves behind

Ask of every test: **what does it skip that the real caller cannot?**

- A browser test that dispatches events skips hit testing, focus order and
  scroll position.
- An API test that calls the service directly skips routing, serialisation,
  authentication and the rate limiter.
- A test that builds its fixture through the same helper the code uses skips
  whatever that helper gets wrong.

None of those are reasons not to write the test. They are the list of things
that test is not evidence about — and the honest move is to write that list
down next to it, because the alternative is what happened here: a green suite
offered as proof of something it had never examined.

The defect was found by a person opening the page and trying to use it, fifteen
minutes after it was declared live.

---

## Coverage, used honestly

96% was the number when it was first measured, and the uncovered 4% was the
interesting 4%:

- the **health endpoint** — no test at all
- the bare `Exception` handler — "the thing that decides whether an
  unanticipated bug leaks a stack trace", never executed
- both cursor failure branches
- `move`'s copy of the chain-coverage guard (`create`'s was covered; the
  duplicate was not)

A well-covered file can still have nothing behind the five lines that matter.
The total is a smoke alarm, not a verdict — **read the `Missing` column.**

The floor is 99.5 with branch coverage on, and it ratchets.

**It was 98, and the comment setting it claimed fourteen lines were "genuinely
out of reach from a test".** Review checked each one. The claim held for
exactly one. Three were contradicted inside the same change — a test that
patches `delete_node` to return 0 sat a few hundred lines from an identical
construct declared unreachable, and one of my own comments explained that
returning `[]` from `lock_ancestry` "would trip the deleted-between-read-and-lock
check first", which is as close to admitting a line is reachable as a comment
gets.

Eleven tests later, the honest number is **one**: `raise AssertionError(
"unreachable: the loop either returns or raises")`, which is unreachable by
construction. The rest were real failure paths — the row went away between the
read and the write, the lock could not be taken, a constraint the service
believes it already prevented fired anyway. The paths that run on the worst
day, and the ones I had written off.

> **"Hard to reach from a test" and "cannot be reached" are different claims.**
> Only the second one belongs in a comment, and I put the first one in two book
> chapters and an interview answer.

Two other things that comment got wrong, which are worth naming because they
are the same mistake in a smaller key: `--fail-under=98` compares the *rounded*
total, so the real floor was 97.5 and five statements could go missing in
silence; and statement coverage says a line ran, not which way it went —
`if exc.status >= 500:` read as fully covered the entire time its true arm had
never once executed. Both are fixed with `--precision=2` and `--cov-branch`.

---

## The isolation gap

Worth its own note, because it is the one a hostile reviewer will look for.

Nothing in the HTTP suite had ever involved a second user. The test actor is
always id 1 under `RESTART IDENTITY`, so **every assertion in the file still
held if the function that decides who is calling returned a hard-coded 1.**

The whole isolation story — the thing that decides whether one person can read
another's files — was untested at the layer facing the internet, in a file of
contract tests that otherwise looked thorough.

Six routes now get driven against another owner's node and must answer 404.
Proven by mutation: with the caller's identity wrong, a listing came back
`['mine', 'theirs']`.

---

## Questions you will be asked

**Q31. You have 187 tests. How do you know they are any good?**
I do not know it from the count. I know it because every guard has been
reverted and watched go red, and the runs are recorded with ids in
`docs/MUTATIONS.md`. That is also how I know five of them were not good: they
passed against the bug they were written for, and mutation is what said so.
The count I would quote is the other one — five tests that could not fail,
found and fixed.

**Q32. Why not just require 100% coverage?**
Because it buys a worse test suite. Fourteen lines here are genuinely out of
reach — an unreachable assertion, a backstop that fires only after two other
checks have failed, and races that need a precise interleaving against a real
database. Demanding 100% means either deleting those guards or writing tests
that execute them without asserting anything, and the second is how a suite
fills up with tests that cannot fail. The floor is 98, it ratchets upward, and
the exclusions are enumerated where the floor is set.

**Q33. Your coverage is 98% and you still shipped a blocking defect. So what is it for?**
Different jobs. Coverage answers "is there a test behind this line" — it found
the health endpoint, the catch-all handler and both cursor branches, all of
which had nothing. It cannot answer "is this line correct", and the M3a defect
was two correct statements with a snapshot boundary between them, every line
covered. That needed a reader. The useful framing is that coverage is a floor
under the obvious failures so that review can spend its attention on the
non-obvious ones.

---

**Next:** [Chapter 15 · Containers and CI](15-containers-and-ci.md)
