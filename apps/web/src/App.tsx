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
          "The server is up but it cannot reach its database. Waiting will not help with this " +
          "one. If you are seeing it, something is genuinely broken and I would like to know.",
      };
    }
    return { kind: "failed", status: error.status, canWake: isColdStart(error), message: error.message };
  }
  return {
    kind: "failed",
    canWake: true,
    message: "I could not reach the server. Nothing answered at all.",
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
      // **A wake already in flight wins, and the second caller says nothing.**
      //
      // The first version of this branch reported the failure instead, and
      // that is a defect you can see: React's StrictMode invokes effects
      // twice in development, so the second `whoAmI()` also 502s, finds the
      // ref set, and replaces the waiting panel with
      // `request failed with 502 (HTTP 502)` -- the exact string this
      // milestone exists to delete -- while the first wake keeps polling
      // invisibly behind it. Found by Gate 3, which rendered the component
      // inside StrictMode; the browser suite cannot see it at all, because
      // Playwright serves a production build where StrictMode does not
      // double-invoke.
      //
      // It is not only a development concern: pressing "Wake it and try
      // again" during a wake is the same race with a user's finger instead
      // of StrictMode.
      if (waking.current) return;
      if (!isColdStart(error)) {
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
            <p className="tag">A folder tree you can drag things around in. I rebuilt it from scratch.</p>
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
            <p className="muted">Checking whether you are already signed in.</p>
          </div>
        )}

        {state.kind === "waking" && (
          <div className="panel center">
            <span className="spinner" aria-hidden="true" />
            <h2 className="panel-h">Waking the server up</h2>
            <p className="muted">
              It runs on a free plan, so it goes to sleep when nobody has used it for fifteen
              minutes. Starting it again takes about half a minute. Nothing is broken; it is just
              booting.
            </p>
            <div className="wake-bar" aria-hidden="true">
              <i style={{ width: `${String(Math.min(100, (state.seconds / budgetSeconds) * 100))}%` }} />
            </div>
            {/*
              The live region is this one line and nothing else, and it says
              the elapsed time rounded to ten seconds.

              `role="status"` on the whole panel re-announced forty words on
              every poll -- about twenty-two times across a ninety-second
              wait, which is not progress reporting, it is a screen reader
              talking over itself. Rounding means roughly one announcement
              every ten seconds, which is what a person would want to be
              told.
            */}
            <p className="mono-small" role="status" aria-live="polite">
              Still waking. About {Math.max(0, Math.round(state.seconds / 10) * 10)} seconds so
              far. I give up at {budgetSeconds}.
            </p>
            <p className="mono-small" aria-hidden="true">
              {state.seconds}s elapsed
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
        <span>Built with Flask, PostgreSQL, React and TypeScript.</span>
        <a href="https://github.com/vinay1500/file-explorer">Read the code on GitHub</a>
      </footer>
    </div>
  );
}
