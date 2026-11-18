import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api/client";
import { signInAsGuest, signOut, whoAmI, type WhoAmI } from "./api/session";

/**
 * The shell, until M6.4 puts a tree inside it.
 *
 * What it is already doing is the part that was missing from the plan: it
 * resolves a session before showing anything, and it shows *every* state
 * rather than only the happy one. The April 2025 client had one state. It
 * called an API that was returning 500, caught the error, wrote it to
 * `console.error`, and rendered an empty tree forever -- so a visitor saw a
 * working-looking application with no data and no explanation. That is F-01,
 * and it is a UI bug as much as an infrastructure one.
 */

type State =
  | { kind: "waking" }
  | { kind: "anonymous" }
  | { kind: "signed-in"; who: WhoAmI }
  | { kind: "failed"; message: string; status?: number };

function describe(error: unknown): State {
  if (error instanceof ApiError) {
    if (error.status === 503) {
      return {
        kind: "failed",
        status: 503,
        message: "The API is up but cannot reach its database. This is not a cold start.",
      };
    }
    return { kind: "failed", status: error.status, message: error.message };
  }
  // A thrown TypeError from `fetch` is the network itself failing, which on a
  // free instance usually means the first request timed out while the server
  // was waking. Saying so is more useful than "Failed to fetch".
  return {
    kind: "failed",
    message:
      "Could not reach the API. On a free instance the first request after a sleep can take " +
      "up to a minute; try again.",
  };
}

export function App() {
  const [state, setState] = useState<State>({ kind: "waking" });
  const [busy, setBusy] = useState(false);

  const resolve = useCallback(async () => {
    try {
      const who = await whoAmI();
      setState(who ? { kind: "signed-in", who } : { kind: "anonymous" });
    } catch (error: unknown) {
      setState(describe(error));
    }
  }, []);

  useEffect(() => {
    void resolve();
  }, [resolve]);

  const guest = async () => {
    setBusy(true);
    try {
      await signInAsGuest();
      await resolve();
    } catch (error: unknown) {
      setState(describe(error));
    } finally {
      setBusy(false);
    }
  };

  const leave = async () => {
    setBusy(true);
    try {
      await signOut();
      setState({ kind: "anonymous" });
    } catch (error: unknown) {
      setState(describe(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="shell">
      <h1>File Explorer</h1>

      {state.kind === "waking" && (
        <p className="note" role="status">
          Waking the server. A free instance sleeps after fifteen idle minutes and can take up to
          a minute to come back.
        </p>
      )}

      {state.kind === "anonymous" && (
        <>
          <p className="note">No session. The demo needs one, and getting one takes a click.</p>
          <button type="button" onClick={() => void guest()} disabled={busy}>
            {busy ? "Opening a session…" : "Continue as guest"}
          </button>
          <p className="muted">
            A guest is a real account with a seeded tree, not a shared demo login. It owns its own
            rows and can see nobody else&rsquo;s.
          </p>
        </>
      )}

      {state.kind === "signed-in" && (
        <>
          <p className="note ok" role="status">
            Signed in as {state.who.email ?? "a guest"}.
          </p>
          <button type="button" onClick={() => void leave()} disabled={busy}>
            {busy ? "Signing out…" : "Sign out"}
          </button>
          <p className="muted">
            The tree is not rebuilt yet &mdash; that is the next task. This page exists to prove
            the session path, which is the thing the previous client could never do.
          </p>
        </>
      )}

      {state.kind === "failed" && (
        <>
          <p className="note bad" role="alert">
            {state.message}
            {state.status !== undefined && <span className="muted"> (HTTP {state.status})</span>}
          </p>
          <button type="button" onClick={() => void resolve()} disabled={busy}>
            Try again
          </button>
        </>
      )}
    </main>
  );
}
