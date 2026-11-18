import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * The proxy is the point of this file, not a convenience.
 *
 * In production the browser never talks to the API directly: Vercel rewrites
 * `/api/*` to the Render service, which is what makes every request
 * *same-origin* and therefore what lets a `SameSite=Lax` session cookie work
 * at all (decision D-22). The API sends no CORS headers, because under that
 * arrangement there is nothing to allow.
 *
 * So a dev server that pointed the browser straight at the deployed API would
 * be testing a different application: cross-origin, no CORS headers, no
 * cookie. Every request would fail, and it would fail in a way that looks like
 * a backend bug. This proxy reproduces the production shape locally - one
 * origin, `/api/*` forwarded - so what works here works there.
 *
 * `changeOrigin` rewrites the Host header to the target, which Render needs to
 * route to the right service. `secure` stays on: the target is HTTPS and there
 * is no reason to accept a bad certificate.
 */
const API_TARGET = process.env.VITE_API_TARGET ?? "https://file-explorer-api-xgxr.onrender.com";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
        secure: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
