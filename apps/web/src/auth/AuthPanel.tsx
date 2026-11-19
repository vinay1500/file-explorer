import { useState } from "react";
import { ApiError } from "../api/client";
import { register, signIn, signInAsGuest } from "../api/session";
import { isColdStart, wakeApi } from "../api/wake";
import { MIN_PASSWORD_LENGTH, faultIn } from "./credentials";

/**
 * The front door.
 *
 * **Guest stays the primary action** — it is one click, it seeds a tree, and
 * an interviewer with thirty seconds will not fill in a form. The account
 * form sits underneath it, closed, for the visitor who wants to see that
 * sessions are real.
 *
 * What this component must never do is claim more than the API told it. The
 * server answers **202 to every registration**, identical body, so that
 * nobody can use the endpoint to ask whether an address has an account. So
 * "create account" does not report success: it registers, then signs in with
 * the same pair, and reports the result of *that* — which reveals exactly what
 * a sign-in reveals and not one bit more.
 */

type Mode = "sign-in" | "create";

interface Props {
  /** Called once a session exists, so the shell can re-resolve it. */
  readonly onSession: () => Promise<void> | void;
  readonly onFailure: (error: unknown) => void;
}

function messageFor(error: unknown, mode: Mode): string {
  if (!(error instanceof ApiError)) {
    return "That request did not reach the server. Try again.";
  }
  if (error.status === 401) {
    return mode === "create"
      ? "That address is already taken, or the password does not match it."
      : "That email and password do not match an account.";
  }
  if (error.status === 429) {
    return "Too many attempts from this address. Wait a minute and try again.";
  }
  if (error.status === 422) {
    return error.problem?.detail ?? "The server would not accept those details.";
  }
  return error.message;
}

export function AuthPanel({ onSession, onFailure }: Props) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fault, setFault] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "guest" | "form">(null);

  const guest = async () => {
    setBusy("guest");
    setFault(null);
    try {
      await signInAsGuest();
      await onSession();
    } catch (error: unknown) {
      // **A 502 here is not retried automatically, and that is deliberate.**
      //
      // Creating a guest is a POST. A 502 from the edge means the response
      // never came back; it does not mean the request never arrived. Sending
      // it again could leave two guest accounts and two seeded trees behind
      // for one click. So: wake the service with a GET, then ask the person
      // to press the button again, and let the second account be a second
      // decision rather than a side effect of ours.
      if (isColdStart(error)) {
        setFault("The server was asleep. Waking it…");
        try {
          await wakeApi();
          setFault("The server is awake now. Press Continue as guest again.");
        } catch (wakeError: unknown) {
          onFailure(wakeError);
        }
        return;
      }
      onFailure(error);
    } finally {
      setBusy(null);
    }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const wrong = faultIn(email, password);
    if (wrong !== null) {
      setFault(wrong);
      return;
    }
    setBusy("form");
    setFault(null);
    try {
      if (mode === "create") await register(email, password);
      await signIn(email, password);
      await onSession();
    } catch (error: unknown) {
      // Shown in the form rather than replacing the whole screen: a wrong
      // password is not a broken application, and blowing the shell away for
      // one would lose what was typed.
      setFault(messageFor(error, mode));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="gate">
      <h2 className="gate-h">Open the demo</h2>
      <p className="gate-lede">
        A guest account is a real account with its own seeded tree &mdash; not a shared login.
        It owns its own rows and can see nobody else&rsquo;s.
      </p>

      <button type="button" className="primary big" onClick={() => void guest()} disabled={busy !== null}>
        {busy === "guest" ? "Opening a session…" : "Continue as guest"}
      </button>

      <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Hide the account form" : "or use an email address and password"}
      </button>

      {/*
        One place for a message, and it lives outside the form -- the guest
        button can fail too, and a notice rendered inside a collapsed form is
        a notice nobody reads.
      */}
      {fault !== null && (
        <p className="note bad" role="alert">
          {fault}
        </p>
      )}

      {open && (
        <>
          {/*
            The mode switch sits outside the <form> on purpose. It is not a
            field -- it chooses which request the one submit button makes --
            and leaving it inside gave the form two buttons reading "Sign in",
            which is ambiguous to a screen reader walking the form and was
            ambiguous to the test that had to pick one of them.
          */}
          <div className="seg" role="group" aria-label="Sign in or create an account">
            <button
              type="button"
              className={mode === "sign-in" ? "seg-on" : ""}
              aria-pressed={mode === "sign-in"}
              onClick={() => {
                setMode("sign-in");
                setFault(null);
              }}
            >
              Sign in
            </button>
            <button
              type="button"
              className={mode === "create" ? "seg-on" : ""}
              aria-pressed={mode === "create"}
              onClick={() => {
                setMode("create");
                setFault(null);
              }}
            >
              Create account
            </button>
          </div>

        <form
          className="gate-form"
          aria-label="Account details"
          onSubmit={(e) => void submit(e)}
        >
          <label className="field">
            <span>Email</span>
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>

          <label className="field">
            <span>Password</span>
            <input
              type="password"
              autoComplete={mode === "create" ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>

          <p className="hint">
            At least {MIN_PASSWORD_LENGTH} characters. There is no &ldquo;one capital, one
            digit&rdquo; rule: length is the only part of a password that reliably predicts
            strength.
          </p>

          <button type="submit" className="primary" disabled={busy !== null}>
            {busy === "form"
              ? "Working…"
              : mode === "create"
                ? "Create account and sign in"
                : "Sign in"}
          </button>

          {mode === "create" && (
            <p className="hint">
              The API answers the same way whether or not the address was free, so that nobody can
              use this form to find out who has an account. If the address is taken, the sign-in
              that follows will say so.
            </p>
          )}
        </form>
        </>
      )}
    </div>
  );
}
