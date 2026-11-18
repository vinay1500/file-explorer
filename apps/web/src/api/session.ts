import { ApiError, request, setCsrfToken } from "./client";
// Through `contract`, not `schema`: one door, and the door is the file that
// asserts the generated types still mean what this application needs.
import type { Credentials, Session, WhoAmI } from "./contract";

export type { WhoAmI };

/**
 * Sign-in, sign-up, guest and sign-out.
 *
 * Every one of these sets or clears the in-memory CSRF token as part of the
 * same call that changes the session, because the two are one fact. Leaving
 * that to the caller is how a client ends up holding a token for a session it
 * no longer has.
 */

function adopt(session: Session): Session {
  setCsrfToken(session.csrf_token ?? null);
  return session;
}

export async function signIn(credentials: Credentials): Promise<Session> {
  return adopt(await request<Session>("/api/v1/auth/login", {
    method: "POST",
    body: credentials,
  }));
}

/**
 * The demo's front door. No form, no email, one request.
 *
 * It exists because an interviewer with thirty seconds will not sign up, and
 * the alternatives were worse: a shared demo account with printed credentials
 * is one password for every visitor, and read-only unauthenticated access
 * would put a second, unscoped path into the data this project spent a
 * milestone isolating. A guest is *exactly* a user -- same table, same foreign
 * keys, same owner predicate -- so nothing about the isolation story changes.
 */
export async function signInAsGuest(): Promise<Session> {
  return adopt(await request<Session>("/api/v1/auth/guest", { method: "POST" }));
}

/**
 * Registration answers 202 whether or not the address was taken, so there is
 * nothing to branch on and nothing to report back about the address. A
 * different answer for a taken address is an account-existence oracle
 * available to anyone, which is the thing the whole auth surface is shaped to
 * avoid.
 */
export async function register(credentials: Credentials): Promise<void> {
  await request<{ accepted?: boolean }>("/api/v1/auth/register", {
    method: "POST",
    body: credentials,
  });
}

export async function signOut(): Promise<void> {
  try {
    await request<void>("/api/v1/auth/logout", { method: "POST" });
  } finally {
    // Cleared even when the request failed. If logout 401s the session was
    // already gone; if it 403s the token was stale. In both cases holding on
    // to it is strictly worse than dropping it.
    setCsrfToken(null);
  }
}

/**
 * Who this session belongs to, or `null` when there is none.
 *
 * `null` rather than a thrown error, because "nobody is signed in" is an
 * ordinary state at start-up and not an exceptional one. Anything that is not
 * a 401 still throws: a 503 is not the same as being signed out, and treating
 * it as such would show a sign-in form to someone whose database is simply
 * unreachable.
 */
export async function whoAmI(): Promise<WhoAmI | null> {
  try {
    const who = await request<WhoAmI>("/api/v1/auth/me");
    setCsrfToken(who.csrf_token ?? null);
    return who;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}
