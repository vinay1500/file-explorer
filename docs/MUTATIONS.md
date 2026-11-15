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

1. On a throwaway branch, revert exactly one behaviour.
2. Push. A draft pull request is needed, because CI runs on `main` and on pull
   requests, not on every branch.
3. Read which tests went red. **Exactly the ones that describe that behaviour,
   and no others** — a mutation that reddens everything localises nothing.
4. Close the pull request, delete the branch.

Not run locally. It needs real PostgreSQL and the full suite, and this project
has no local runtime by design (D-13). CI is where every command is proven.

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

Rows 8, 10 and 17 are the ones worth reading.

**8** is a test that could not fail, found by mutation and by nothing else.

**10** is a test environment hiding the bug class it was supposed to expose:
this suite's PostgreSQL is initialised `--locale=C`, so the default collation
*is* byte order and an ordering test passes whether or not anything was ever
pinned. The guard that works reads `pg_index`.

**17** is why coverage is a gate and not a report. Deleting the health-check
tests left the suite entirely green — the same health endpoint whose
predecessor returned a constant and stayed green for seventeen months while
the database was gone.
