import type { Problem } from "./contract";

/**
 * `/api/v1/health` is deliberately outside the OpenAPI document: it exists for
 * the platform's probe, not for clients, and publishing it would invite one to
 * depend on it. So its success shape is declared here by hand.
 *
 * Its *failure* shape is not hand-written, because every failure in this API is
 * an RFC 9457 problem document and that one is published. Importing `Problem`
 * from the generated contract means a change to the error shape breaks this
 * file at build time rather than at runtime.
 */
export interface Health {
  status: "ok" | "degraded";
  db: "ok" | "unreachable";
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: Problem | null,
  ) {
    super(problem?.title ?? `request failed with ${status}`);
    this.name = "ApiError";
  }
}

/**
 * Relative URL, deliberately.
 *
 * In production Vercel rewrites `/api/*` to the API, which is what makes the
 * request same-origin and therefore what lets a `SameSite=Lax` session cookie
 * work at all. An absolute URL bypasses that, makes every request cross-site,
 * and the cookie is refused at storage time -- which is exactly what the April
 * 2025 client did, and why it could never have signed in. CI greps the built
 * bundle for an `onrender.com` URL to keep it that way.
 *
 * `credentials: "same-origin"` is already the default for `fetch`, but a
 * default the session depends on is worth writing down.
 */
export async function probeHealth(): Promise<Health> {
  const response = await fetch("/api/v1/health", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    let problem: Problem | null = null;
    try {
      problem = (await response.json()) as Problem;
    } catch {
      // A failure that is not a problem document is still a failure. The
      // status is what the caller needs; swallowing the parse error here is
      // the opposite of what the old client did, which swallowed the *request*
      // error and rendered an empty tree forever.
      problem = null;
    }
    throw new ApiError(response.status, problem);
  }
  return (await response.json()) as Health;
}
