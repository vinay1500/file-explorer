# Chapter 22 · The same tour, as a story

> Volume VI · The Tour
> Written at M19 · the same journey as [chapter 21](21-reading-the-code.md), with no line
> numbers · last verified 2026-11-21

[Chapter 21](21-reading-the-code.md) is a map: eleven stops, each with a file and a line. Maps
are what you want on the second reading. On the first reading you want somebody to tell you what
happens.

So here is what happens, once, when a person drags a folder out of another folder and drops it
on the empty space below the tree. No line numbers. Read it in one sitting; it takes about ten
minutes.

---

## Somebody opens the page

A stranger clicks a link. Nothing happens for twenty-nine seconds.

This is the first thing to understand about this project, because it is the first thing anybody
experiences: **the server is asleep.** It is a free instance, it shuts down after fifteen idle
minutes, and waking it takes about half a minute. For seventeen months the old version handled
this by showing a blank screen and then an empty folder tree, and every single person who
clicked that link on a résumé concluded the application was broken. They were nearly right.

Now the page counts the seconds out loud. It says a free instance sleeps, it says it is waiting,
and it shows how long it has been waiting. That is the whole fix, and it took **four separate
attempts** to get right — not because the waiting was hard to write, but because the first three
attempts fixed the wrong thing. Twice the page waited on a cheaper request than the one it
needed. Once the per-attempt timeout was shorter than the server's actual wake time, so no
attempt could ever finish. And the fourth time, the real cause: *a request sent the normal way
cannot wake the server at all.* It has to be knocked on directly first, at its own address, with
a request whose answer nobody reads.

Three of those four fixes were verified against a fake. The fourth was verified by leaving the
thing alone for sixteen minutes and then actually trying it. That gap — between testing the
mechanism and testing the experience — is the single most expensive habit in this whole project,
and it cost three days.

Then the tree appears.

---

## The gesture

The stranger has a folder called `Holiday` sitting inside `Photos`, and wants it at the top
level. They press the mouse down on it, drag it below the last row, and let go.

That drop happens in a region most applications do not have. The library drawing this tree
signals *put it at the top level* by noticing the pointer is over the container rather than over
any row — and for the first four days after the rebuild, **the container had no spare pixels.**
It was exactly as tall as the rows inside it. There was nowhere to drop.

A browser test covered that gesture and passed every single run, because a test aims at an
element and a person aims at a point. The test said "deliver a drop event to the container" and
the browser obliged. A person has to get a mouse there first.

So now the container has a visible band below the last row, and the test that guards it does not
name the target at all — it asks the browser what is underneath a few hundred coordinates and
fails if the answer is not the container. Before the fix, nought of 1,368 sampled points
belonged to it. After, 465 of 465.

---

## Three lines decide whether the feature exists

The drop lands in the component holding the tree, and it has one job: work out where the folder
is going and tell the server.

Here is where the project's defining bug lived, and it is smaller than anybody expects.

**The drag library calls the top level `0`. The server calls it `null`.**

That is it. Those two are not interchangeable: `0` is a perfectly good id for a real folder, and
`null` is not a value the library has any concept of. The old client looked up the drop target by
id, found nothing for the top level, and quietly gave up. No error. Nothing in a log. The folder
slid back to where it came from, and the person shrugged and tried again.

For eighteen months, **items could go into folders and nothing could come back out**, and the
reason was that the client had no way to say the word "root".

There is now one small function whose entire purpose is to translate between the two, and the
test that matters asserts it returns `null` and not `0`. Because if it returned `0`, the server
would be asked to put the folder *inside folder number zero*, which does not exist, and the
feature would silently stop working again — in a brand new way.

---

## Leaving the browser

The request goes through exactly one function, and the first thing that function does is refuse
anything that does not start with `/api/`.

That refusal is load-bearing, and the reason is one of the stranger things in this project. The
site lives at one address and the server lives at another. A browser treats those as different
*sites*, not merely different addresses — and a login cookie marked the safe way is **thrown
away at the moment the browser tries to store it** when it arrives from a different site. Not
rejected later. Not visible in any error. Simply never kept.

So the old client, which had the server's full address typed into it, could never have logged
anybody in. Every request would have arrived anonymous, and the failure would have looked
exactly like a server bug.

The fix is four lines of configuration: the site forwards anything starting with `/api/` to the
server on the browser's behalf. Now the browser only ever talks to one address, the cookie is
kept for the obvious reason, and the whole category of cross-site permissions — the thing people
spend afternoons configuring — **disappears instead of being configured.** The server sends none
of those headers at all, because it never needs to.

And the client throws an error rather than politely failing if anybody ever types a full address
again, because that is a programmer's mistake and it should be loud.

---

## Arriving

The request lands in the server, and before any of this project's code about trees runs, three
small things happen in a very specific order.

The order is the interesting part. Three separate bugs in this project were **ordering** bugs,
and all three had the same shape: a piece of code that was completely correct and ran at the
wrong moment, so it did nothing at all while appearing to be in place.

One was a header that gets stripped from responses — registered in what looked like the obvious
position, which meant it ran *before* the thing that adds the header, so it stripped nothing and
the header went out on every response anyway. Dead code that read as a working guard.

One was the rate limit. It is supposed to count requests per signed-in person. It was registered
after the code that works out who is signed in, so it asked "who is this?" before anybody had
answered, and counted everybody as anonymous. **Every test but one passed.**

One was error formatting. This server promises that every failure comes back as the same tidy
shape. A library it uses installs its own competing handler, and whichever is registered later
wins — so for a while, errors this project raised came back tidy and errors the framework raised
came back as web pages. Exactly the split the code existed to prevent, caused by two lines being
in the wrong order.

Then the cookie is turned into a person. The cookie holds 32 random bytes; the table stores only
a fingerprint of them, so if the database ever leaked, nothing in it could be used to log in.

---

## The route that does almost nothing

The request reaches the piece of code responsible for moving folders, and it is four lines long.
Ask who is calling, read the destination out of the request, call one function, return what it
gives back.

**That shortness is a rule, not tidiness.** The hard tests in this project — the ones that open
two database connections at once and make them fight — talk straight to the tree logic. They
cannot reach anything that lives in a web route. So a rule written in a web route is a rule
those tests cannot test, and it would be the first thing to break.

---

## The hard part

Now we are at the thing this whole project is about, and it is worth slowing down.

The obvious danger when moving a folder is moving it **into itself** — dragging `Photos` into
`Photos/Holiday`. Do that and the rows all still exist, every rule the database knows is
satisfied, and the folder is simply gone, because nothing can reach it from the top any more.
Not deleted. Unreachable. That is what shipped in April 2025, and the only check it had was
whether you had dropped a folder onto *itself* — which catches one case out of an infinite
number.

So the fix is to walk up from the destination and see whether the folder being moved appears
anywhere on the way. One query, any depth.

**And that is still not enough**, which is the genuinely interesting part.

Picture two people. One drags `Docs` into `Work`. At the same instant, the other drags `Work`
into `Docs`. Both are at the top level, so each move is perfectly legal. Each person's check
runs, looks at the tree, and honestly reports no problem. Both are telling the truth. Then both
writes land, and the two folders are now inside each other — a loop, built out of two
individually correct operations.

A check is a statement about **a moment**. By the time the write happens, the moment is gone.

The fix is to make the check and the write see the same tree: lock the rows involved first, then
look again, then decide. Two details took real work to get right.

The first is which rows to lock. The instinct is to lock the destination and everything above it
— and that is *almost* right, and the test built on that story passes against a broken
implementation. The lock that actually does the work is the one on **the folder being moved**,
because any two moves that could jointly make a loop always have one's moved folder somewhere in
the other's destination chain.

The second is subtler, and nothing warns you about it. After taking the lock, you must ask the
question *again, as a brand new query*. Reusing the answer you got while taking the lock feels
like an obvious saving. It is wrong: the lock stops others changing those rows, but the answer
you are holding was computed before you waited, and the tree may have changed while you were
waiting. The test that proves this needs four folders across two trees moved in a ring, because
with only two folders it passes either way.

There is a third detail, and it came from a reviewer rather than from a test. The code locked a
chain of folders it had worked out *before* waiting for the lock — so if somebody else's move
landed during the wait, it ended up holding the wrong set of rows and measuring a different set.
Every individual guard still fired when tested in isolation. They were simply guarding a chain
that had moved. **Reverting a guard proves it bites; it cannot prove it is in the right place.**

---

## The write, and the way back

One row changes one column. That is the entire point of how this data is stored: a folder
remembers who its parent is, and nothing remembers its children, so moving a folder with ten
thousand things inside it is still one row.

The other three common ways to store a tree each make reading faster and that write dramatically
worse — one rewrites every single item underneath, one renumbers a large part of the table, one
deletes and reinserts a row for every ancestor of every item. All three are reasonable choices
for data people mostly read. This is data people **drag**, so the single-row write wins and the
cost is paid in reads, which need a recursive query rather than a simple one.

The transaction commits. The row comes back. And the client, rather than patching its own copy
of the tree, **throws its copy away and asks again.**

That is slower and it is deliberate. The fast version — change the screen immediately, send the
request, undo it if the server disagrees — was planned, written into the schedule, and then cut.
Its failure mode is showing somebody a tree the server does not have, which is precisely the
category of bug this entire project exists because of. Trading correctness for the appearance of
speed, in a rebuild about a tree that silently stopped matching reality, would have been a joke
at my own expense.

The folder is at the top level. The whole round trip, with the server already awake, takes about
a third of a second.

---

## What the story is actually about

Read back over it and notice what the hard parts had in common. Almost nothing here was a
mistake in logic — nobody wrote a loop backwards or got an arithmetic test wrong.

- The client **could not express a value**: it had no way to say "root".
- The pixels **were not there**: a region with no area.
- The code was **correct but ran at the wrong moment**: three ordering bugs.
- The check was **true when it ran** and false when it mattered.
- The guard was **in the wrong place** while firing perfectly.
- The test **aimed at an element** where a person aims at a point.

Six categories, and every one of them produces a system that looks like it works. Nothing
crashes. No error appears in any log. The tests are green. The one thing all six have in common
is that **the failure is silent**, and the only reliable way they were found was somebody
opening the thing and using it.

Ten defects in this project were found that way — by a person clicking around for fifteen
minutes. In the same stretch, twelve adversarial review gates, 284 server tests, 88 browser-free
client tests and 28 real browser tests found **none of those ten.**

That is not an argument against the tests. The tests are why those ten are the *only* ten, and
why each of them stayed fixed. It is an argument about what tests are for: they hold the ground
you have already taken. They do not take new ground, and a green suite is not evidence that
anybody has tried the thing.

---

**Next:** [Chapter 1 · Why hierarchies are hard](01-why-hierarchies-are-hard.md) — if you came
here first, the book starts there.

[← Chapter 21 · Reading the code](21-reading-the-code.md) · [Back to the book](README.md)
