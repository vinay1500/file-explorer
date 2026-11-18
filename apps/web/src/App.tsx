import { useEffect, useState } from "react";
import { probeHealth, type Health } from "./api/health";

type State =
  | { status: "waking" }
  | { status: "ready"; health: Health }
  | { status: "failed"; reason: string };

/**
 * The scaffold's only job, for now: prove the proxy path works end to end.
 *
 * It calls `/api/v1/health` as a **relative** URL. In the dev server Vite
 * forwards that to the API; in production Vercel rewrites it. Neither case
 * involves the browser knowing the API's hostname, which is the whole point --
 * an absolute URL here is what broke the April 2025 client, because it bypassed
 * the rewrite and made every request cross-site, so the session cookie was
 * refused at storage time and nothing ever signed in.
 *
 * The waking state is not decoration either. A free instance spins down after
 * fifteen idle minutes and takes 30-60 seconds to come back. The original
 * frontend called an API that was returning 500, swallowed the error in
 * `console.error`, and rendered an empty tree forever -- so a visitor saw a
 * working-looking application with no data and no explanation (F-01). Showing
 * the wait, and showing the failure, is the fix for that.
 */
export function App() {
  const [state, setState] = useState<State>({ status: "waking" });

  useEffect(() => {
    let cancelled = false;
    probeHealth()
      .then((health) => {
        if (!cancelled) setState({ status: "ready", health });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const reason = error instanceof Error ? error.message : "unknown error";
        setState({ status: "failed", reason });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="shell">
      <h1>File Explorer</h1>
      {state.status === "waking" && (
        <p className="note" role="status">
          Waking the server. A free instance sleeps after fifteen idle minutes and takes up to a
          minute to come back.
        </p>
      )}
      {state.status === "ready" && (
        <p className="note ok" role="status">
          API reachable. Database: {state.health.db}.
        </p>
      )}
      {state.status === "failed" && (
        <p className="note bad" role="alert">
          Could not reach the API: {state.reason}
        </p>
      )}
      <p className="muted">
        The tree interface is not rebuilt yet. This page exists to prove the proxy path, which is
        what the previous client got wrong.
      </p>
    </main>
  );
}
