import { ApiError, request, setCsrfToken } from "./client";
// Through `contract`, not `schema`: one door, and the door is the file that
// asserts the generated types still mean what this application needs.
import type { Session, WhoAmI } from "./contract";

export type { WhoAmI };

/**
 * Guest sign-in, and sign-out.
 *
 * There is deliberately no `signIn` or `register` here. D-24 chose the
 * one-click guest session *over* a form: an interviewer with thirty seconds
 * will not sign up, and a shared demo account with printed credentials is one
 * password for every visitor. Shipping a `signIn` nothing can reach would be
 * the dead-code finding (F-15) this milestone just closed, in a new file.
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
