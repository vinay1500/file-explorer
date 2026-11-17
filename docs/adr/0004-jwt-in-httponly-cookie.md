# 0004 — The session credential, and where it lives

Accepted · 2026-11-14 · **amended 2026-11-17 (D-21)** · implemented at M5

> This file is still named `0004-jwt-in-httponly-cookie.md`. The name is kept so existing links
> keep working, and because the decision it records is half right: the credential is a cookie,
> and that part never changed. What changed is what the cookie carries.

## Context
The frontend needs an authenticated session. Two questions, and only one of them was asked the
first time.

**Where does the credential live?** A token in `localStorage` is readable by any script that
reaches the page, so one XSS becomes a silent account takeover lasting until the token expires.
A cookie marked `httpOnly` is not reachable by script at all. Because a cookie is sent
automatically, choosing it makes CSRF protection **mandatory rather than optional**.

**What is the credential?** This was not asked on 14 November, and it is the question an
interviewer asks first.

## Decision

**An opaque session id in an httpOnly, Secure, SameSite cookie, with a CSRF token bound to the
session row.**

As originally written, this said *"JWT in an httpOnly, Secure, SameSite=Lax cookie, with a
double-submit CSRF token"*. Both halves of that were amended on 17 November, before any of it
was built, by a review that ran against the design rather than the code.

### Why not a JWT

A JWT buys exactly one thing: verification without touching the database. That is worth paying
for when verification has to happen where the database is not — across services, at an edge,
under load that would not survive the extra read.

This application queries PostgreSQL on every request from a single free instance. The benefit
cannot be collected here, and every cost still is: no revocation, algorithm pinning, clock skew,
and a refresh-rotation design. The original decision was already half-stateful — it promised a
refresh token, and a refresh token that cannot be revoked or reuse-detected needs a row anyway.
The table was arriving whatever happened, and the JWT would have been a session id with extra
steps.

What the row buys back: **logout is a `DELETE`** rather than a request that a browser forget
something; **deleting a user ends their sessions**, through `ON DELETE CASCADE`; and nothing has
to be pinned — no `alg`, no issuer, no leeway, no decision about whether a refresh token is
acceptable on a resource route.

The last of those is larger than it reads. The same review enumerated seven ways a token-reading
`require_actor()` could return an id it should not, including a subject spelled `1_0`, which
`int()` reads as `10`. **Six of the seven stop existing** when nothing parses a
caller-influenced string into an id: the actor comes out of a `bigint` column through a foreign
key. The seventh is closed by the cascade.

### Why not a double-submit CSRF token

Double-submit compares a header against a cookie — which is to say, it compares the request to
itself. Anyone who can write a cookie for the site satisfies it: a sibling subdomain, or a
network attacker on a plain-HTTP sibling.

Being stateful removes the reason to accept that. The session row is already being read to
authenticate, so comparing the header against a value stored **in the row** costs nothing extra
and is a synchronizer token: bound to the session, unforgeable by anyone who cannot read the
table.

### The cookie has to be able to reach the API

`SameSite=Lax` is correct here **only because** the browser reaches the API through a rewrite on
the frontend's own origin (D-22). Across two registrable domains a `Lax` cookie is refused at
storage time, so sign-in would appear to succeed and the next request would be a 401 — and every
test would pass, because a test client does not enforce `SameSite` and `localhost` to
`localhost` is same-site. `create_app` refuses to start on the contradiction.

## Consequences
The credential is not reachable by script, so an XSS cannot exfiltrate it. Sessions are
revocable, individually and per account. There is no signing key to rotate and no token format
to version. Every authenticated request costs one indexed primary-key read, which is the price
of all of the above and is paid on a request that was going to query the database anyway.

## Alternatives
**`localStorage` + an `Authorization` header.** No CSRF exposure, and simpler. Rejected: any
script that reaches the page can read it, so one XSS becomes a silent takeover lasting until the
token expires. The failure we are choosing is a forged request blocked by a token the attacker
cannot read, over an undetectable takeover.

**A JWT, kept.** Rejected above. It would have required this record to state and accept a
non-revocable window, and a refresh token in a row regardless.

## Anticipated questions
*"`SameSite=Lax` already blocks cross-site POSTs — why also carry a token?"* Because `SameSite`
is a browser behaviour, not a server control: older browsers, unusual clients and some redirect
paths differ, and a server-side check does not depend on the client behaving.

*"Why not a JWT? Everyone uses a JWT."* Answered above, and the short version is one sentence:
a JWT buys statelessness, statelessness is worth paying for when verification must happen
without the database, and mine queries the database on every request from one instance — so the
session is a row and logout is a `DELETE`.
