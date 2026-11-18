# apps/web

The browser half of File Explorer. Vite 7, React 19, TypeScript 5.

## State

**Built, not yet deployed.** This directory was Create React App until 18 November 2026. What
exists today is the session client, the drag-and-drop tree with rename, delete and create, 25
unit tests and 2 browser tests. `file-explorer-frontend.vercel.app` still serves the April 2025
bundle — connecting the deployment is the last task.

**Guest only, by decision.** There is no sign-in form and no registration, and that is D-24
rather than an omission: an interviewer with thirty seconds will not sign up, and a shared demo
account with printed credentials is one password for every visitor. One click opens a real
account with a seeded ten-node tree, owning its own rows and able to see nobody else's.

The April 2025 client is **not** ported and will not be. It called `/tree`, `/node`,
`/node/{id}` and `/move/{id}`, none of which exist in the API's published document; it hardcoded
the API's absolute hostname, which bypasses the rewrite and makes every request cross-site so the
session cookie is refused; and it sent neither cookie nor CSRF header. Every request it can make
fails. It is in the git history — `git log -- apps/web/src/components` — which is why the
history was grafted rather than squashed.

## Running it

```bash
npm install
npm run dev     # http://localhost:5173
```

`npm run dev` proxies `/api/*` to the deployed API. To point it somewhere else:

```bash
VITE_API_TARGET=http://localhost:5000 npm run dev
```

## Why the proxy is not a convenience

In production the browser never talks to the API directly. Vercel rewrites `/api/*` to the Render
service, which makes every request **same-origin** — and that is what lets a `SameSite=Lax`
session cookie work at all. The API sends no CORS headers, because under that arrangement there
is nothing to allow.

A dev server pointing the browser straight at the deployed API would therefore be running a
different application: cross-origin, no CORS headers, no cookie. Every request would fail, and it
would look like a backend bug. The proxy reproduces the production shape, so what works here
works there.

This is the mistake the previous client made, and it is the reason the demo could never have
signed in even if the rest of it had worked.

## Commands

| | |
|---|---|
| `npm run dev` | Dev server with the `/api` proxy |
| `npm run build` | Typecheck, then build to `dist/` |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | ESLint 9 flat config, type-aware |
| `npm run api-types` | Regenerate `src/api/schema.d.ts` from the API's OpenAPI document |
