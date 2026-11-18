import type { Problem } from "./contract";

/**
 * The one place a request is made.
 *
 * Three properties hold here and nowhere else, which is the reason this file
 * exists rather than scattering `fetch` through components:
 *
 * 1. **Every URL is relative.** Vercel rewrites `/api/*` to the API, which is
 *    what makes the request same-origin and therefore what lets a
 *    `SameSite=Lax` session cookie work at all. An absolute URL bypasses the
 *    rewrite, makes the request cross-site, and the cookie is refused at
 *    storage time. That is what the April 2025 client did, and it is why it
 *    could never have signed in.
 *
 * 2. **Unsafe methods carry the CSRF token.** It is a synchronizer token held
 *    in the session row, not a double-submit cookie -- the server compares the
 *    header against the row, not against another thing the browser sent.
 *
 * 3. **A failure is an `ApiError` carrying the parsed problem document**, never
 *    a swallowed `console.error`. F-01 was a frontend that caught its errors,
 *    logged them where nobody looks, and rendered an empty tree forever.
 */

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const CSRF_HEADER = "X-CSRF-Token";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: Problem | null,
  ) {
    super(problem?.title ?? `request failed with ${status}`);
    this.name = "ApiError";
  }

  /** The machine-readable code, when the server sent a problem document. */
  get code(): string | undefined {
    return this.problem?.code;
  }
}

/**
 * The CSRF token lives in memory for the lifetime of the tab, and deliberately
 * not in `localStorage`.
 *
 * `localStorage` outlives the session cookie. After a sign-out, or after the
 * session expires server-side, a stored token would still be sent -- producing
 * 403s that look like a bug in the application rather than like a session that
 * ended. Memory and the cookie then expire together, which is the only pairing
 * that cannot drift.
 */
let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  /** Set false to stop the one automatic retry. Used by the retry itself. */
  retryOnCsrfFailure?: boolean;
}

async function readProblem(response: Response): Promise<Problem | null> {
  try {
    return (await response.json()) as Problem;
  } catch {
    // A failure that is not a problem document is still a failure, and the
    // status is what the caller needs. Returning null says "no detail", which
    // is different from pretending the request succeeded.
    return null;
  }
}

/**
 * `path` must start with `/api/`. Anything else is a programming error rather
 * than a runtime condition, so it throws rather than returning a failure: a
 * component asking for `https://…` has made the F-01 mistake and should not
 * get a polite error object for it.
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  if (!path.startsWith("/api/")) {
    throw new Error(`refusing a non-relative request to ${path}: it would bypass the rewrite`);
  }

  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (!SAFE_METHODS.has(method) && csrfToken !== null) headers[CSRF_HEADER] = csrfToken;

  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers,
    ...(options.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
  });

  if (response.ok) {
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  const problem = await readProblem(response);

  // A 401 means the session is gone, whatever the caller thought. Clearing the
  // token here rather than at the call site means no caller can forget.
  if (response.status === 401) {
    csrfToken = null;
    throw new ApiError(401, problem);
  }

  // A 403 on an unsafe method is a CSRF mismatch, and the honest cause is
  // usually a token this tab has had since before the session rotated. Fetch
  // the current one and retry **exactly once** -- a loop here turns one stale
  // token into an unbounded request storm against the endpoint with the
  // tightest rate limit in the API.
  if (
    response.status === 403 &&
    !SAFE_METHODS.has(method) &&
    (options.retryOnCsrfFailure ?? true)
  ) {
    const refreshed = await refreshCsrfToken();
    if (refreshed) {
      return request<T>(path, { ...options, retryOnCsrfFailure: false });
    }
  }

  throw new ApiError(response.status, problem);
}

/**
 * Ask the server which token this session expects. Returns false when there is
 * no session, which is the answer that stops the retry above.
 */
async function refreshCsrfToken(): Promise<boolean> {
  try {
    const who = await request<{ csrf_token?: string }>("/api/v1/auth/me");
    csrfToken = who.csrf_token ?? null;
    return csrfToken !== null;
  } catch {
    csrfToken = null;
    return false;
  }
}
