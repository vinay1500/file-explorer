# Security

Rules for every contributor to **File Explorer**
(`vinay1500/file-explorer`). Scope: a multi-user folder manager with per-user private trees.
No payments, no file contents, no third-party data, no PII beyond an email address.

Rules that can be tested are tested. `apps/api/tests/test_security.py` is the enforcement
mechanism for most of this document — a rule that is only written down is a rule that drifts.

---

## 1. Core rules

- Never commit passwords, API keys, tokens, private keys, or connection strings.
- Secrets live in environment variables, supplied by the host's secret store. `.env` is
  gitignored; `.env.example` is committed and contains **names and shapes only**, never values.
- Treat every request body, query parameter and path segment as hostile.
- Validate on the server. The frontend's validation is a convenience, never a control.
- Use parameterised queries everywhere, including the recursive CTEs. No SQL is ever built by
  string concatenation or f-string, even from a value that "cannot" be user-controlled.
- Enforce authorization on the server for every action on every request.
- Least privilege: the application's database role owns no DDL rights in production. Migrations
  run as a separate role, in a separate step.
- Never disable TLS, authentication, authorization, or a failing security test to get past an error.
- Never return a stack trace, an ORM error, or a raw exception message to a client.

---

## 2. This project's actual attack surface

The threats that apply here, each with how it is handled and the test that proves it.

### 2.1 Insecure direct object reference — the main one

Node ids are sequential integers. `GET /api/v1/nodes/41` is a guess away from `/42`.

- Every query is scoped by `owner_id` taken from the session, never from the request body.
- A node that exists but belongs to someone else returns **404, not 403**. A 403 confirms the
  row exists and leaks the shape of another user's tree. The two cases must be
  indistinguishable — same status, same body, same timing class.
- `new_parent_id` on a move is validated for ownership before anything else. A cross-owner move
  is a 404 on the destination, not a cycle check failure.
- Tests: `test_other_users_node_returns_404`, `test_cannot_move_into_another_users_folder`,
  `test_list_never_returns_foreign_nodes`.

### 2.2 Node name validation

Names are not file paths today, but they are rendered in a UI and may one day be used to build
one. Validated on the server, at creation and at rename:

- Length 1–255 characters after trimming; empty and whitespace-only rejected (`422`).
- Rejected outright: `/`, `\`, NUL and all C0 control characters.
- Rejected as whole names: `.` and `..`.
- Unicode normalised to NFC before the uniqueness check, so two visually identical names cannot
  both exist in one folder.
- Rendered as text, never as HTML. React escapes by default; the rule is that nothing introduces
  `dangerouslySetInnerHTML` into the tree view.
- Tests: `test_name_rejects_path_separators`, `test_name_rejects_control_chars`,
  `test_name_unicode_normalised`, `test_empty_name_returns_422`.

### 2.3 Mass assignment

`PATCH /api/v1/nodes/{id}` accepts exactly one field: `name`. A marshmallow schema with
`unknown = RAISE` rejects anything else, so `owner_id`, `parent_id`, `id` and `created_at` can
never be set through a rename. Moves go through their own endpoint with their own checks.
Test: `test_patch_rejects_unknown_fields`.

### 2.4 Cross-origin requests

`CORS(app)` with no arguments reflects whatever `Origin` the caller sends — verified against the
current deployment, which echoed `https://evil.example.com` back in
`access-control-allow-origin`. Combined with cookie authentication this is a live
cross-site request vector.

- An explicit allowlist, read from `CORS_ALLOWED_ORIGINS`, with no wildcard.
- `supports_credentials=True` only for listed origins.
- Test: `test_cors_rejects_unlisted_origin` asserts the header is absent for an unknown origin.

### 2.5 Session handling

- JWT in an httpOnly, Secure, SameSite=Lax cookie. Not localStorage — localStorage is readable
  by any script that gets onto the page, so a single XSS becomes full account takeover.
- Because the credential is a cookie, CSRF protection is mandatory: a double-submit token
  required on every mutating request, compared against the cookie.
- Short access-token lifetime with refresh; logout clears the cookie server-side.
- Tests: `test_mutation_without_csrf_token_rejected`, `test_cookie_flags_set`.

### 2.6 Authentication endpoints

- Passwords hashed with argon2id. No plaintext, no reversible encoding, never logged.
- Rate limiting on register, login and refresh, by IP and by account.
- Login returns the same response and the same timing class for an unknown email and a wrong
  password. No user enumeration.
- Test: `test_login_does_not_enumerate_users`.

### 2.7 Denial of service through the tree

The structure itself is the resource an attacker can abuse.

- Depth capped at 64 (invariant I7), enforced on create and on move.
- Subtree reads are depth-limited and paginated. No endpoint can be asked for an unbounded tree.
- A per-user node count ceiling, returning `429`.
- Recursive CTEs carry an explicit depth guard so a malformed tree cannot spin forever.
- Tests: `test_depth_cap_enforced`, `test_subtree_requires_depth_bound`.

### 2.8 Schema and administrative endpoints

The current deployment exposes `GET /create-tables`, unauthenticated, which runs
`db.create_all()`. This is deleted and does not return in any form.

- No HTTP route performs DDL, ever.
- Schema changes are Alembic migrations run as a deployment step.
- `debug=True` does not appear in any file. Debug is read from the environment and defaults off.
- Test: `test_no_ddl_routes_registered` walks the URL map and fails if a suspicious route exists.

---

## 3. Logging and errors

- Structured JSON, one request id per request, bound to every line.
- Never logged: passwords, tokens, cookie values, `Authorization` headers, connection strings.
- Node **names** are user content: log node ids, not names.
- Client responses use the Problem Details shape from `docs/ARCHITECTURE.md` §6 and carry no
  internal detail. The request id is returned so a report can be correlated with a log line.

---

## 4. Handling credentials

- Never ask for, print, reveal or invent a credential.
- Never copy production data into a log, a test fixture, a screenshot or a public issue.
- A command that would print a secret to the terminal is written differently: print only the
  shape of a value — scheme, host and database name — never the user or password.
- Get explicit sign-off before changing authentication, authorization, session handling or
  rate limiting.
- Get sign-off before running a migration anywhere but the local Compose database, before
  deleting data, before force-pushing or rewriting history, and before touching a deployment.
- Explain every security-relevant change and add the test alongside it.

### Never paste a secret anywhere outside its secret store

Anything beginning `postgres://` or `postgresql://` (the password is inside the URL), the
contents of any `.env`, an unredacted dashboard screenshot, `ghp_` / `github_pat_` / `sk-` /
`Bearer` tokens, the output of `gh auth token`, `env` or `printenv`, the JWT signing secret, or
`git remote -v` when a remote has a token embedded in it.

## 5. Secret rotation

- Rotate on decommission, not only on compromise. Any secret that has lived in a dashboard for
  a long time, or has ever been pasted anywhere, is rotated when the service it belongs to is
  replaced.
- The `DATABASE_URL` currently configured on the Render service is treated as compromised and is
  rotated as part of the migration to the new database, regardless of whether anything leaked.
- A secret that reaches a git commit is rotated first and scrubbed from history second. Rotation
  is the fix; history rewriting is cleanup.

---

## 6. Review checklist

- [ ] No secret, credential, or `.env` in the diff.
- [ ] Every new query is scoped by `owner_id` from the session.
- [ ] A foreign or missing resource returns 404, never 403.
- [ ] Input validated server-side with a schema; `unknown = RAISE`.
- [ ] Errors use Problem Details and expose nothing internal.
- [ ] No new route performs DDL or administrative action without authentication.
- [ ] New dependencies reviewed; `pip-audit` and `npm audit` clean in CI.
- [ ] A test exists for the security property being claimed.
