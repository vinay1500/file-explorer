# 0004 — Session token in an httpOnly cookie

Proposed · 2026-11-14 · implemented at M5

## Context
The frontend needs an authenticated session. Two storage options, and neither is free.

## Decision
JWT in an `httpOnly`, `Secure`, `SameSite=Lax` cookie, with a double-submit CSRF token
required on every mutating request.

## Consequences
The token is not reachable by script, so an XSS cannot exfiltrate it. Because cookies are
sent automatically, CSRF protection becomes **mandatory rather than optional**.

## Alternatives
**`localStorage` + `Authorization` header.** No CSRF exposure, and simpler. Rejected: any
script that reaches the page can read it, so one XSS becomes silent account takeover lasting
until the token expires. The failure we are choosing is a forged request blocked by a token
the attacker cannot read, over an undetectable takeover.

## Anticipated question
*"`SameSite=Lax` already blocks cross-site POSTs — why also carry a token?"* Because
`SameSite` is a browser behaviour, not a server control: older browsers, unusual clients and
some redirect paths differ, and a server-side check does not depend on the client behaving.
