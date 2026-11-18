# apps/web

The browser half of File Explorer. Vite 7, React 19, TypeScript 5.

## State

**Scaffold only.** This directory was Create React App until 18 November 2026 and the rebuild is
in progress — see milestone M6. What exists today is the project skeleton and a page that proves
the API is reachable through the proxy. The tree, drag and drop, and the session client are the
next three tasks.

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
