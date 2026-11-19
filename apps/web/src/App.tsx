import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api/client";
import { signOut, whoAmI, type WhoAmI } from "./api/session";
import { isColdStart, wakeApi, WAKE_BUDGET_MS, type WakeState } from "./api/wake";
import { AuthPanel } from "./auth/AuthPanel";
import { Explorer } from "./tree/Explorer";
import { MoonGlyph, SunGlyph } from "./ui/icons";
import { applyTheme, initialTheme, storeTheme, type Theme } from "./ui/theme";

/**
 * The shell: a session, a theme, and every state the demo can actually be in.
 *
 * The April 2025 client had one state. It called an API that was returning
 * 500, caught the error, wrote it to `console.error`, and rendered an empty
 * tree forever -- so a visitor saw a working-looking application with no data
 * and no explanation. That is F-01.
 *
 * The version before this one had four states and still failed the first
 * thing a visitor does, which is open a cold link. A free instance sleeps
 * after fifteen idle minutes; the next request is answered **502 by the edge**
 * until the container is up. The shell showed `request failed with 502
 * (HTTP 502)` and stopped -- technically true, useless, and wrong about whose
 * fault it was. Waiting for a cold start is what `wakeApi` is for, and this
 * component is what shows the waiting.
 */

type State =
  | { kind: "resolving" }
  | { kind: "waking"; seconds: number }
  | { kind: "anonymous" }
  | { kind: "signed-in"; who: WhoAmI }
  | { kind: "failed"; message: string; status?: number; canWake: boolean };

function describe(error: unknown): State {
  if (error instanceof ApiError) {
    if (error.status === 503) {
      return {
        kind: "failed",
        status: 503,
        canWake: false,
        message:
          "The API is running but cannot reach its database. Waiting will not fix this one — " +
          "it is the condition the April 2025 deployment was in for seventeen months.",
      };
    }
    return { kind: "failed", status: error.status, canWake: isColdStart(error), message: error.message };
  }
  return {
    kind: "failed",
    canWake: true,
    message: "Could not reach the API. Nothing answered the request.",
  };
}

export function App() {
  const [state, setState] = useState<State>({ kind: "resolving" });
  const [busy, setBusy] = useState(false);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  // One wake at a time. Two overlapping polls against a waking instance is
  // twice the requests for no extra information.
  const waking = useRef(false);

  useEffect(() => {
    applyTheme(theme);
    storeTheme(theme);
  }, [theme]);

  const resolve = useCallback(async (): Promise<void> => {
    try {
      const who = await whoAmI();
      setState(who ? { kind: "signed-in", who } : { kind: "anonymous" });
    } catch (error: unknown) {
      if (!isColdStart(error) || waking.current) {
        setState(describe(error));
        return;
      }
      waking.current = true;
      setState({ kind: "waking", seconds: 0 });
      try {
        await wakeApi((s: WakeState) => {
          setState({ kind: "waking", seconds: Math.round(s.elapsedMs / 1000) });
        });
        const who = await whoAmI();
        setState(who ? { kind: "signed-in", who } : { kind: "anonymous" });
      } catch (wakeError: unknown) {
        setState(describe(wakeError));
      } finally {
        waking.current = false;
      }
    }
  }, []);

  useEffect(() => {
    void resolve();
  }, [resolve]);

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

  const budgetSeconds = Math.round(WAKE_BUDGET_MS / 1000);

  return (
    <div className="page">
      <header className="top">
        <div className="brand">
          <span className="mark" aria-hidden="true" />
          <div>
            <h1>File Explorer</h1>
            <p className="tag">A hierarchical folder tree, with the moves the server refuses</p>
          </div>
        </div>
        <div className="top-right">
          {state.kind === "signed-in" && (
            <>
              <span className="who" title={state.who.email ?? undefined}>
                {state.who.email ?? "a guest"}
              </span>
              <button type="button" className="ghost" onClick={() => void leave()} disabled={busy}>
                {busy ? "Signing out…" : "Sign out"}
              </button>
            </>
          )}
          <button
            type="button"
            className="icon-btn"
            aria-label={theme === "light" ? "Switch to dark" : "Switch to light"}
            onClick={() => setTheme(theme === "light" ? "dark" : "light")}
          >
            {theme === "light" ? <MoonGlyph /> : <SunGlyph />}
          </button>
        </div>
      </header>

      <main className="shell">
        {state.kind === "resolving" && (
          <div className="panel center" role="status">
            <span className="spinner" aria-hidden="true" />
            <p className="muted">Checking for a session…</p>
          </div>
        )}

        {state.kind === "waking" && (
          <div className="panel center" role="status" aria-live="polite">
            <span className="spinner" aria-hidden="true" />
            <h2 className="panel-h">Waking the server</h2>
            <p className="muted">
              This instance is free, so it sleeps after fifteen idle minutes and the first request
              starts it again. It usually takes 30&ndash;60 seconds.
            </p>
            <div className="wake-bar" aria-hidden="true">
              <i style={{ width: `${String(Math.min(100, (state.seconds / budgetSeconds) * 100))}%` }} />
            </div>
            <p className="mono-small">
              {state.seconds}s elapsed &middot; giving up at {budgetSeconds}s
            </p>
          </div>
        )}

        {state.kind === "anonymous" && (
          <div className="panel">
            <AuthPanel onSession={resolve} onFailure={(e) => setState(describe(e))} />
          </div>
        )}

        {state.kind === "signed-in" && <Explorer />}

        {state.kind === "failed" && (
          <div className="panel center">
            <h2 className="panel-h bad">That did not work</h2>
            <p className="note bad" role="alert">
              {state.message}
              {state.status !== undefined && (
                <span className="muted"> (HTTP {state.status})</span>
              )}
            </p>
            <button type="button" className="primary" onClick={() => void resolve()}>
              {state.canWake ? "Wake it and try again" : "Try again"}
            </button>
          </div>
        )}
      </main>

      <footer className="bottom">
        <span>
          Flask &middot; PostgreSQL &middot; React &middot; TypeScript &mdash; one origin, session
          cookie, no CORS
        </span>
        <a href="https://github.com/vinay1500/file-explorer">The code</a>
      </footer>
    </div>
  );
}
