# The mutation record

Every guard in this repository has been proven to fail. Not argued to, not
reviewed into shape — reverted on a throwaway branch, pushed, and watched go
red in CI, with the branch deleted afterwards.

This file is the record, because the practice is only worth anything if it is
auditable. A claim that tests are good is worth nothing; a run id is worth
something.

## Why

Four times in this project a test passed against the bug it was written to
catch — five if you count the one that indexed the answer it was meant to
assert. Each time, the test read correctly, the reviewer read it correctly, and
it still proved nothing. The only thing that has reliably found them is
breaking the code and watching.

The failure mode is always the same shape: **the test reaches past the thing it
is testing**, and asserts something about a lower layer that was never in
doubt. A locking primitive works. PostgreSQL raises the sqlstate. The dict key
exists. None of that is what the test is named for.

> The question to ask before writing an assertion is not "is this true when the
> code is right" but **"what differs when it is wrong"**. If the answer is "it
> blocks" or "the key is present", that is not an observable — both orderings
> block, and the key is present either way.

## How

1. On a throwaway branch, revert exactly one behaviour. **Assert that the edit
   applied** — a mutation that silently does nothing is indistinguishable from
   a guard that works, and has twice been mistaken for one here.
2. Push. A draft pull request is needed, because CI runs on `main` and on pull
   requests, not on every branch.
3. Read which tests went red. **Exactly the ones that describe that behaviour,
   and no others** — a mutation that reddens everything localises nothing.
4. Close the pull request, delete the branch.

Not run locally. It needs real PostgreSQL and the full suite, and nothing in
this project installs on the development machine — a deliberate choice, with
the trade written up in [chapter 15](book/15-containers-and-ci.md). CI is where
every command is proven.

## The record

| # | Mutation | Expected | What happened | Run |
|---|---|---|---|---|
| 1 | `delete-orphan` restored; cross-owner walk unguarded | 2 tests red | 2 red, exactly those | `43509491192` |
| 2 | `fileConfig` default restored (loggers disabled) | logging guard red | red, naming `file_explorer.services.tree`, `flask_migrate` | `43571276522` |
| 3 | `_MAX_ATTEMPTS = 1` | the 2 retry tests red | red; the 2 non-retry branches stayed green | `43571375169` |
| 4 | `create`'s chain lock removed | concurrency test red | red — **all 54 invariant tests still passed**, which is the point | `43701459536` |
| 5 | Off-by-one depth cap, truncation guard removed, I5 check removed | 3 red | 3 red, one per mutation | `43701553539` |
| 6 | Children orphaned instead of cascaded (the original bug) | I3 tests red | 3 red | `43702231215` |
| 7 | Count after the delete; truncation guard removed | 3 red | 3 red, incl. `assert 0 == 5` | `43702327190` |
| 8 | `delete`'s lock removed | lock test red | **ALL GREEN** — the test called the primitive directly and never went through `delete` | `43702462370` |
| 9 | Same mutation, against the rewritten test | red | red: *"delete reported 2 nodes but removed 3"* — the number its docstring predicted | `43702716183` |
| 10 | Index collation unpinned | ordering test red? | **only** the `pg_index` test red. The ordering test passed, because CI runs `--locale=C` | `43706926545` |
| 11 | No lookahead row, no page clamp, id-only cursor | 3 red | 4 red, incl. `assert 205 == 200` | `43707027254` |
| 12 | NFC removed, character deny-list removed | many red | 17 red, incl. a driver `DataError` proving the NUL claim | `43708261775` |
| 13 | ASCII-only strip, no quota, hardcoded bounds, env re-read | 4 areas red | 8 red | `43708370903` |
| 14 | Ancestor truncation check, file-as-parent, name category rule | 3 areas red | 16 red | `43713319481` |
| 15 | Chain coverage check removed; raw driver error restored | 2 red | 2 red — node at level 7 again, and a `DataError` escaping | `43713422501` |
| 16 | Query strictness, exception headers, and the caller's identity | 3 red | 8 red; a listing returned `['mine', 'theirs']` | `43719982842` |
| 17 | The three health-check tests deleted | coverage floor red | **every test still passed**; only the floor caught it, at 97% | `43723085822` |

| 18 | Eight M4 guards removed at once | 8 red | **5 red.** Two of the eight were misapplied by the mutation script itself — one hit the first of two identical lines, the other left a decorator's indentation behind and produced a syntax error | `43727207915` |
| 19 | The two, reapplied precisely | 2 red | 1 red. The other exposed something else entirely — see below | `43727478366` |
| 20 | Health exemption removed, against health's own bucket | red | red | `43727642755` |
| 21 | `new_parent_id` narrowed from `number \| null` to `number` in the generated API types | the web build red | red: `TS2344: Type 'false' does not satisfy the constraint 'true'` | run locally against `tsc 5`, see below |
| 22 | `padding-bottom` removed from the tree's root container | the hit-test red | red, and **two others with it**: the band is positioned absolutely, so without the padding it lay over the rows and intercepted clicks on their buttons | local, Chromium |
| 23 | `isColdStart` widened to include 503 | the "stops at once on 503" test red | red: 2 of 9 | local, Vitest |
| 24 | `MIN_PASSWORD_LENGTH` 12 → 8 | the OpenAPI agreement test red | red, naming both numbers | local, Vitest |
| 25 | the 401-after-register message replaced with "Account created." | the "never claims the account was created" test red | red | local, Vitest |
| 26 | the move-to-top button's condition inverted, `!atTop` → `atTop` | its browser test red | red, and only that one | local, Chromium |
| 27 | 502 dropped from `isColdStart` | the cold-start browser test red | red; the 503 test stayed green, which is the distinction that matters | local, Chromium |
| 28 | `/api/v1/auth/login` changed to a typo | the session test red | red. **Before Gate 3 this mutation passed 46 of 46**, because the only test touching sign-in mocked the whole module | local, Vitest |
| 29 | `signIn` stops adopting the CSRF token | the session test red | red. Also passed 46 of 46 before Gate 3, and the result would have been a session that cannot perform a single write | local, Vitest |
| 30 | the `mode === "create"` guard deleted, so every sign-in also registers | the AuthPanel test red | red. The mutation is a real defect: unauthenticated account creation for any typed address, and two of ten attempts a minute per press | local, Vitest |
| 31 | the theme default inverted to dark | theme and App tests red | red, 5 of 9. Nothing covered D-27 before Gate 3 | local, Vitest |
| 32 | **the StrictMode wake race reintroduced** (`if (waking.current) return` folded back into the error branch) | the App test red | red, and only that one. This is the BLOCK Gate 3 found; the browser suite **cannot** see it, because Playwright serves a production build where StrictMode does not double-invoke | local, Vitest |
| 33 | the `signal` spread removed from `client.ts` | the client test red | red. The only functional change in that file, and it was deletable with 46 of 46 green | local, Vitest |
| 34 | the `.tree-root::after` rule deleted entirely | the band test red | red, and **only** that one &mdash; which is the finding: the drop still works without it, so the band is paint, and the affordance needed its own test | local, Chromium |
| 35 | `subtreeFullyLoaded` inverted in the delete dialog | the unopened-folder test red | red. Deleting the call instead does not typecheck, which is a second guard | local, Chromium |
| 36 | **The cold-start wait restored to its old shape**: poll `/api/v1/health`, then make one unprotected call to `/api/v1/auth/me` | the new session-retry test red | red, and only that one. This is the defect Vinay hit on 19 November, reproduced on demand | local, Vitest |

Rows 8, 10, 17, 18, 22, 32 and 36 are the ones worth reading.

**Row 22 found a defect in the fix it was testing.** Deleting the padding was
meant to prove only that the hit-test test could fail. It reddened two further
tests, because the drop band is an absolutely positioned `::after` and with the
padding gone it covered the rows beneath it and swallowed their clicks. The
band is now `pointer-events: none`: hit testing reaches the `<ul>` through its
own padding box either way -- a pseudo-element is never what
`elementFromPoint` returns -- so the drop still works, and the band can no
longer intercept anything if the padding above it is ever reduced. Verified by
re-running the suite, not by reasoning about it.

**Rows 28 to 35 are Gate 3's.** Six of them passed the whole suite before that
review: the login path could be mistyped, the CSRF token could stop being
captured, every sign-in could silently register an account, the theme could
invert, the abort signal could be deleted and the visible drop band could be
removed, all with a green run. Row 32 is the one to remember -- the defect it
restores is invisible to the browser suite by construction, because Playwright
serves a production build and the bug only exists under React's development
StrictMode.

**8** is a test that could not fail, found by mutation and by nothing else.

**10** is a test environment hiding the bug class it was supposed to expose:
this suite's PostgreSQL is initialised `--locale=C`, so the default collation
*is* byte order and an ordering test passes whether or not anything was ever
pinned. The guard that works reads `pg_index`.

**17** is why coverage is a gate and not a report. Deleting the health-check
tests left the suite entirely green — the same health endpoint whose
predecessor returned a constant and stayed green for seventeen months while
the database was gone.

**18** is a mistake in the method rather than in the code, and it is the easier
one to make. Two of the eight mutations never applied: a `str.replace(..., 1)`
hit the first of two identical lines, and another removed `@limiter.exempt`
without its indentation and produced a syntax error that read, at a glance,
like a test failure. **Assert that the mutation applied.** A mutation that
silently does nothing looks exactly like a guard that works.

Run 19 then showed something the first attempt had hidden: removing the health
exemption *still* did not redden its test, because Flask-Limiter applies
default limits **per endpoint**. The test spent one route's budget and asked
another, which passes either way. The configured "120 per minute" is per
endpoint per caller, not a global ceiling, and the configuration now says so.
That is a fact about the system found by a mutation that failed to fail.


## 21 — the first mutation that is not a test

The twenty above all redden a test. This one reddens a **build**, and the
difference is the point.

`contract.ts` asserts, in the type system, that `null` remains assignable to
`NodeMove["new_parent_id"]`. There is no test to run: either the file compiles
or it does not. Narrowing the field to a plain `number` produces

```
error TS2344: Type 'false' does not satisfy the constraint 'true'.
```

which is as red as a failing test and arrives earlier.

**Why it is worth asserting at all**, when CI already regenerates the types and
fails on any diff: drift detection would *catch* the narrowing and then invite
somebody to approve it. The regenerated file would contain
`new_parent_id: number`, the diff would be three lines, and it reads as
tightening a type rather than as deleting a feature. It is F-06 — a client that
cannot say "move this to the root" — and it is invisible unless you already
know that is what `null` means here.

This one was verified on a throwaway file rather than a throwaway branch,
because the assertion is type-level and needs no database, no CI run and no
application: two three-line files, one narrowed, `tsc --noEmit --strict` on
each. The cheapest proof in this document, and the record would be worth less
without it.
