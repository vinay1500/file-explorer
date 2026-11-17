"""Password hashing, and the parameters chosen for the machine it runs on.

argon2id, because it is the current recommendation and because it is memory-hard
-- the cost of a guessing attack scales with RAM as well as with time, which is
what makes a GPU farm a poor investment against it.

## Why not the library's defaults

`argon2-cffi` defaults to roughly 64 MiB of memory, 3 iterations and 4 lanes.
Those are reasonable numbers on a machine that has memory and cores to spend.
This application is deployed on a **single free instance with about 512 MB of
RAM and a shared CPU**, and the defaults are wrong there in two separate ways:

* **64 MiB per in-flight hash** means a handful of simultaneous logins is most
  of the instance's memory. A memory-hard hash on a small box is a denial of
  service lever pointed at yourself: the attacker spends one request and the
  server spends 64 MiB.
* **4 lanes on a shared vCPU** does not parallelise. It buys none of the
  intended speed-up and costs the scheduling.

So the parameters are **OWASP's minimum recommended argon2id configuration**:
19 MiB of memory, 2 iterations, 1 lane. Not numbers invented here -- a published
floor, which is the difference between "I chose 19456" and "I used what the
library gave me".

The honest reading: this is the weakest configuration anyone recommends, and it
is the right one for this hardware. If the instance grows, the number to raise
first is `memory_cost`, because memory is what attacker hardware finds
expensive. Raising it is also safe at any time -- an encoded hash carries the
parameters it was made with, so old hashes keep verifying and
`needs_rehash` reports which ones to upgrade on next login.

## Why the verification error handling is wider than it looks

`verify()` raises `VerifyMismatchError` for a wrong password, and
`InvalidHashError` for a stored value it cannot parse. **`InvalidHashError`
descends from `ValueError`, not from `Argon2Error`**, so `except
VerificationError` -- the obvious handler -- misses it entirely and the request
becomes a 500. That mattered here: until migration 0003 every row held an empty
`password_hash`, which is exactly the unparseable case, so the obvious handler
would have turned every legacy login into a 500 and made that 500 an existence
oracle. The schema forbids the blank value now, and this module still treats any
unverifiable stored hash as a failed authentication rather than an error,
because **the caller supplied nothing wrong** and a 500 would tell them
something about the row.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# OWASP minimum for argon2id: m=19456 KiB, t=2, p=1.
MEMORY_COST_KIB = 19456
TIME_COST = 2
PARALLELISM = 1

_hasher = PasswordHasher(
    memory_cost=MEMORY_COST_KIB,
    time_cost=TIME_COST,
    parallelism=PARALLELISM,
)


def hash_password(password: str) -> str:
    """Return an encoded argon2id hash, parameters included."""
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """True if `password` matches, False for every other outcome.

    Every failure is the same answer on purpose. A wrong password, a stored hash
    this library cannot parse, and a stored hash made by some other scheme all
    mean "not authenticated" -- and distinguishing them in the response, or by
    raising, hands a caller information about a row they have not proved they
    own.
    """
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError, ValueError):
        # `ValueError` is not redundant with `InvalidHashError`: the latter is a
        # subclass of it, and listing both says the breadth is deliberate rather
        # than a lucky inheritance that a library release could change.
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when a stored hash was made with weaker parameters than current.

    Called after a successful verification, so the password is in hand and can
    be re-hashed at the current cost without asking the user for anything.
    """
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, ValueError):
        # Unparseable, so it cannot be upgraded in place. `verify_password` has
        # already refused it; saying "no rehash needed" here keeps this function
        # from being a second place that decides whether a hash is usable.
        return False
