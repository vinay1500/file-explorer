import { ApiError, request } from "./client";

/**
 * Waiting out a free instance's cold start.
 *
 * Render stops a free service after fifteen idle minutes. The next request
 * reaches the edge, the edge starts the container, and **until the container
 * answers, the edge returns `502`** — for anything from ten seconds to, in
 * practice, well over a minute.
 *
 * ---
 *
 * **This module has been wrong twice, and both times for the same reason: it
 * waited for the wrong thing.**
 *
 * The first version did not wait at all and reported `request failed with 502`
 * after one attempt.
 *
 * The second version polled `/api/v1/health` until it answered, and then made
 * **one unprotected request** for the thing the page actually needs. Vinay hit
 * exactly that on 19 November: the waiting panel counted to 51 seconds, health
 * came up, `/api/v1/auth/me` was still answered `502` by the edge, and the
 * screen printed `request failed with 502 (HTTP 502)` — the precise string this
 * whole milestone exists to delete. Health is `SELECT 1` and exempt from the
 * rate limiter; the first *real* request builds a connection pool and loads the
 * ORM, so a container can serve one and not yet the other.
 *
 * The rule that falls out of it:
 *
 * > **Wait for the operation you actually need, not for a proxy that is
 * > cheaper to ask.** A readiness probe answers a question about itself.
 *
 * So there is no health poll here any more. `keepTrying` retries *the caller's
 * own operation* until it succeeds, and the caller passes the request whose
 * answer it is about to render. `/api/v1/auth/me` carries no rate limit, which
 * is what makes polling it the honest choice rather than an expensive one.
 */

/** A per-attempt ceiling, so one hung connection cannot eat the whole budget. */
const ATTEMPT_TIMEOUT_MS = 15_000;

/**
 * Total time to keep trying.
 *
 * Render's documentation says "up to a minute". The measurement that matters
 * more: on 19 November the instance had not answered a real request after 51
 * seconds. A budget that expires while the thing is still starting produces an
 * error message about a working system, so this is set well past the
 * documented figure.
 */
export const WAKE_BUDGET_MS = 180_000;

const FIRST_DELAY_MS = 1_000;
const MAX_DELAY_MS = 4_000;

export interface WakeState {
  /** How many requests have been made, including the one in flight. */
  readonly attempt: number;
  /** Milliseconds since the first attempt. */
  readonly elapsedMs: number;
}

/**
 * The budget ran out while the service still looked like it was starting.
 *
 * A distinct type, because the *message* matters: "request failed with 502" is
 * true, useless, and wrong about whose fault it is. Anything rendering a
 * failure needs to be able to tell this apart from a server that answered
 * something.
 */
export class StillWaking extends Error {
  constructor(
    readonly waitedMs: number,
    readonly last: unknown,
  ) {
    super(`gave up waiting after ${String(Math.round(waitedMs / 1000))}s`);
    this.name = "StillWaking";
  }
}

/** True for the failures that mean "not up *yet*" rather than "broken". */
export function isColdStart(error: unknown): boolean {
  if (error instanceof StillWaking) return true;
  if (error instanceof ApiError) {
    // 502 and 504 are the edge speaking for a container that is not answering.
    // 503 is **not** here on purpose: `/api/v1/health` answers 503 itself when
    // the process is up and the database is unreachable, which no amount of
    // waiting fixes and which is the condition the April 2025 deployment sat
    // in for seventeen months.
    return error.status === 502 || error.status === 504;
  }
  // A thrown `fetch` is the network itself: DNS, a refused connection, or our
  // own abort. None of them say the service is unhealthy, only that nothing
  // answered in time.
  return error instanceof TypeError || error instanceof DOMException;
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    // The listener is removed on the ordinary path too. `{ once: true }` only
    // removes it when it fires, so without this every completed sleep left a
    // closure attached to the caller's signal.
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("wake cancelled", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export interface KeepTryingOptions {
  budgetMs?: number;
  signal?: AbortSignal;
  now?: () => number;
}

/**
 * Run `attempt` until it succeeds, the budget runs out, or it fails in a way
 * that waiting cannot fix.
 *
 * `attempt` is handed an `AbortSignal` and should pass it through, so a hung
 * connection costs fifteen seconds rather than the whole budget.
 *
 * Throws `StillWaking` when the budget expires, and rethrows anything else
 * untouched — a 503, a 500, a 422 all come straight back, because none of them
 * is a container that has not started.
 */
export async function keepTrying<T>(
  attempt: (signal: AbortSignal) => Promise<T>,
  onState?: (state: WakeState) => void,
  options: KeepTryingOptions = {},
): Promise<T> {
  const budgetMs = options.budgetMs ?? WAKE_BUDGET_MS;
  const now = options.now ?? (() => Date.now());
  const started = now();
  let attempts = 0;
  let delay = FIRST_DELAY_MS;
  let last: unknown = null;

  for (;;) {
    attempts += 1;
    onState?.({ attempt: attempts, elapsedMs: now() - started });
    try {
      return await attempt(AbortSignal.timeout(ATTEMPT_TIMEOUT_MS));
    } catch (error: unknown) {
      last = error;
      if (options.signal?.aborted === true) throw error;
      if (!isColdStart(error)) throw error;
      if (now() - started >= budgetMs) throw new StillWaking(now() - started, error);
    }
    await wait(delay, options.signal);
    delay = Math.min(delay + FIRST_DELAY_MS, MAX_DELAY_MS);
    if (now() - started >= budgetMs) throw new StillWaking(now() - started, last);
  }
}

export interface Health {
  status: string;
  db: string;
}

/**
 * Wake the service without needing anything from it.
 *
 * Used where there is no safe operation to retry: the guest button is a `POST`,
 * and a `POST` that returns 502 may or may not have reached the application, so
 * retrying it could leave two accounts behind. The caller wakes with this, then
 * sends its one request.
 */
export async function wakeApi(
  onState?: (state: WakeState) => void,
  options: KeepTryingOptions = {},
): Promise<Health> {
  return keepTrying<Health>(
    (signal) => request<Health>("/api/v1/health", { signal }),
    onState,
    options,
  );
}
