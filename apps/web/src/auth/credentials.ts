/**
 * The bounds the form enforces before it sends anything.
 *
 * These are copies of the server's rule, and a copy of a rule is a drift
 * waiting to happen — a form that lets eight characters through produces a
 * 422 the visitor cannot act on, and a form that demands twenty rejects
 * passwords the server would have taken.
 *
 * `credentials.test.ts` reads `apps/api/docs/openapi.json` — the committed
 * document the API's own tests keep in step with the schema — and fails when
 * these numbers stop matching it. The copy is allowed to exist because
 * something checks it, which is the same arrangement as the generated API
 * types.
 */

export const MIN_PASSWORD_LENGTH = 12;
export const MAX_PASSWORD_LENGTH = 256;
export const MAX_EMAIL_LENGTH = 320;

/**
 * What is wrong with this pair, or `null` when the server should decide.
 *
 * Deliberately shallow: it checks length and the presence of an `@`, and
 * leaves everything else to the server. Client-side email validation that
 * tries to be clever rejects addresses that are legal, and the server is the
 * one that has to be right.
 */
export function faultIn(email: string, password: string): string | null {
  if (email.trim() === "") return "An email address is needed.";
  if (!email.includes("@")) return "That does not look like an email address.";
  if (email.length > MAX_EMAIL_LENGTH) {
    return `That address is longer than ${String(MAX_EMAIL_LENGTH)} characters.`;
  }
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `The password needs at least ${String(MIN_PASSWORD_LENGTH)} characters.`;
  }
  if (password.length > MAX_PASSWORD_LENGTH) {
    return `The password is longer than ${String(MAX_PASSWORD_LENGTH)} characters.`;
  }
  return null;
}
