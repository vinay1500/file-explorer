import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api/client";
import { signOut, whoAmI, type WhoAmI } from "./api/session";
import { StillWaking, isColdStart, keepTrying, WAKE_BUDGET_MS, type WakeState } from "./api/wake";
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
  // **`request failed with 502` must never reach the screen.** It did, on the
  // give-up path, after M8 had deleted it from the first-attempt path: the
  // waiting panel counted to 51 seconds and then printed the one string this
  // work exists to remove. A status code is not a sentence.
  if (error instanceof StillWaking) {
    return {
      kind: "failed",
      canWake: true,
      message:
        `The server is taking longer than usual to wake up. I waited ` +
        `${String(Math.round(error.waitedMs / 1000))} seconds. It's a free instance, so this ` +
        `happens; another go usually catches it.`,
    };
  }
  if (error instanceof ApiError) {
    if (error.status === 503) {
      return {
        kind: "failed",
        status: 503,
        canWake: false,
        message:
          "The server's up but it can't reach its database. Waiting won't help with this one. " +
          "If you're seeing this, something is genuinely broken and I'd like to know.",
      };
    }
    if (isColdStart(error)) {
      return {
        kind: "failed",
        status: error.status,
        canWake: true,
        message: "The server didn't answer. It may still be starting up.",
      };
    }
    return { kind: "failed", status: error.status, canWake: false, message: error.message };
  }
  return {
    kind: "failed",
    canWake: true,
    message: "I couldn't reach the server. Nothing answered at all.",
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
    // **One operation, retried, with one deadline over the whole thing.**
    //
    // The version before this polled `/api/v1/health` until it answered and
    // then made a single unprotected call to `/api/v1/auth/me`. Health is
    // `SELECT 1` and exempt from the rate limiter; the first real request
    // builds a connection pool and loads the ORM. A starting container can
    // serve one and not the other, and when that happened the screen printed
    // `request failed with 502` after fifty-one seconds of saying "waking".
    //
    // So the thing being waited for is now the thing the page renders.
    // `/api/v1/auth/me` carries no rate limit, which is what makes polling it
    // honest rather than expensive, and a 401 from it is a success.
    if (waking.current) return;
    waking.current = true;
    try {
      const who = await keepTrying(
        (signal) => whoAmI(signal),
        (s: WakeState) => {
          // The first attempt gets no waiting panel: on a warm instance it
          // answers in a third of a second and a flash of "waking the server"
          // would be a lie about what is happening.
          if (s.attempt > 1) {
            setState({ kind: "waking", seconds: Math.round(s.elapsedMs / 1000) });
          }
        },
      );
      setState(who ? { kind: "signed-in", who } : { kind: "anonymous" });
    } catch (error: unknown) {
      setState(describe(error));
    } finally {
      waking.current = false;
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
            <p className="tag">A folder tree you can rearrange by dragging.</p>
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
            <p className="muted">Checking whether you're already signed in.</p>
          </div>
        )}

        {state.kind === "waking" && (
          <div className="panel center">
            <span className="spinner" aria-hidden="true" />
            <h2 className="panel-h">Waking the server up</h2>
            <p className="muted">
              It&rsquo;s on a free plan, so it sleeps when nobody has used it for fifteen
              minutes. Waking it usually takes under a minute, sometimes two. Nothing&rsquo;s
              broken, it&rsquo;s just booting, and I keep asking until it answers.
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
              far.
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
            <h2 className="panel-h bad">That didn&rsquo;t work</h2>
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
