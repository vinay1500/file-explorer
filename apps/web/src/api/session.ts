import { ApiError, request, setCsrfToken } from "./client";
// Through `contract`, not `schema`: one door, and the door is the file that
// asserts the generated types still mean what this application needs.
import type { Session, WhoAmI } from "./contract";

export type { WhoAmI };

/**
 * The four ways a session begins or ends.
 *
 * **D-24 said one-click guest *instead of* a form**, and this file said so:
 * an interviewer with thirty seconds will not sign up, and shipping a
 * `signIn` nothing could reach would have been the dead-code finding (F-15)
 * in a new file. The first half of that still holds and the guest button is
 * still the front door.
 *
 * The second half stopped being true the moment the demo went live: the API
 * has had `POST /auth/register` and `POST /auth/login` since M5, both tested,
 * both rate-limited, both reachable with `curl` — and the only way in through
 * the browser was a button marked *guest*. That reads as a half-finished
 * authentication system rather than a deliberate one, which is the opposite
 * of what M5 was for. **D-26 narrows D-24: guest stays the front door, and
 * the accounts the API already has are now reachable from it.**
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

/**
 * Sign in with an email address and a password.
 *
 * A 401 here is an ordinary answer, not an exception to hide: the pair did not
 * match. The server says the same thing for "no such account" and "wrong
 * password" on purpose, so this function has nothing more specific to offer
 * and does not invent it.
 */
export async function signIn(email: string, password: string): Promise<Session> {
  return adopt(
    await request<Session>("/api/v1/auth/login", {
      method: "POST",
      body: { email, password },
    }),
  );
}

/**
 * Create an account.
 *
 * **The server answers 202 whether or not the address was free**, with an
 * identical body, because a 409 would be a working answer to "does this person
 * have an account" available to anyone. So this returns nothing useful by
 * design, and the caller must not say "account created" — it does not know
 * that. The honest next step is to try signing in with the same pair, which
 * reveals exactly what a sign-in reveals and nothing more.
 */
export async function register(email: string, password: string): Promise<void> {
  await request<{ accepted: boolean }>("/api/v1/auth/register", {
    method: "POST",
    body: { email, password },
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
export async function whoAmI(signal?: AbortSignal): Promise<WhoAmI | null> {
  try {
    const who = await request<WhoAmI>("/api/v1/auth/me", signal ? { signal } : {});
    setCsrfToken(who.csrf_token ?? null);
    return who;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}
