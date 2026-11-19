import { ApiError, request } from "./client";

/**
 * Waiting out a free instance's cold start, which the first version did not do
 * at all.
 *
 * Render stops a free service after fifteen idle minutes. The next request
 * reaches the edge, the edge starts the container, and **until the container
 * answers, the edge returns `502`** — for anything from ten seconds to about a
 * minute. The first client turned that into `request failed with 502
 * (HTTP 502)` and stopped, which is the demo's first impression for anybody
 * who opens the link cold. The server was not broken; it was asleep, and
 * nothing was waiting for it.
 *
 * Two distinctions this module exists to keep:
 *
 * 1. **502/504 and a thrown `fetch` are "still waking". 503 is not.**
 *    `/api/v1/health` answers 503 itself when the process is up and the
 *    database is unreachable — a condition no amount of waiting fixes, and the
 *    one the April 2025 deployment was actually in for seventeen months.
 *    Retrying it would turn a diagnosis into a hang.
 * 2. **Waking is a `GET`, and only ever a `GET`.** The wake runs against
 *    `/api/v1/health`, never against the thing the visitor pressed. A `POST`
 *    that returns 502 may or may not have reached the application, so
 *    retrying `POST /auth/guest` could leave two guest accounts behind; the
 *    caller wakes the service first and then sends its one request.
 */

/** A per-attempt ceiling, so one hung connection cannot eat the whole budget. */
const ATTEMPT_TIMEOUT_MS = 15_000;

/** Total time to keep trying. Render's own documentation says "up to a minute". */
export const WAKE_BUDGET_MS = 90_000;

const FIRST_DELAY_MS = 1_000;
const MAX_DELAY_MS = 5_000;

export interface WakeState {
  /** How many requests have been made, including the one in flight. */
  readonly attempt: number;
  /** Milliseconds since the wake began. */
  readonly elapsedMs: number;
}

/** True for the failures that mean "not up *yet*" rather than "broken". */
export function isColdStart(error: unknown): boolean {
  if (error instanceof ApiError) return error.status === 502 || error.status === 504;
  // A thrown `fetch` is the network itself: DNS, a refused connection, or our
  // own abort. None of them say the service is unhealthy, only that nothing
  // answered in time.
  return error instanceof TypeError || error instanceof DOMException;
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    // The listener is removed on the ordinary path too. `{ once: true }`
    // only removes it when it *fires*, so without this every completed
    // sleep left a closure attached to the caller's signal -- twenty-two of
    // them across a ninety-second wait, all holding a dead timer id.
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

export interface Health {
  status: string;
  db: string;
}

/**
 * Poll `/api/v1/health` until it answers, the budget runs out, or it fails in
 * a way that waiting cannot fix.
 *
 * Resolves when the service is up. Throws the last error otherwise — so the
 * caller can tell "I waited ninety seconds and nothing answered" from "the
 * database is unreachable", which are different sentences for the visitor.
 */
export async function wakeApi(
  onState?: (state: WakeState) => void,
  options: { budgetMs?: number; signal?: AbortSignal; now?: () => number } = {},
): Promise<Health> {
  const budgetMs = options.budgetMs ?? WAKE_BUDGET_MS;
  const now = options.now ?? (() => Date.now());
  const started = now();
  let attempt = 0;
  let delay = FIRST_DELAY_MS;
  let last: unknown = null;

  for (;;) {
    attempt += 1;
    onState?.({ attempt, elapsedMs: now() - started });
    try {
      // The caller's signal is combined with the per-attempt timeout rather
      // than only consulted between attempts: an abort during a request that
      // is already in flight must cancel *that request*, not wait up to
      // fifteen seconds for it to time out first. Gate 3 found this path
      // dead.
      const deadline = AbortSignal.timeout(ATTEMPT_TIMEOUT_MS);
      const signal =
        options.signal === undefined
          ? deadline
          : AbortSignal.any([options.signal, deadline]);
      return await request<Health>("/api/v1/health", { signal });
    } catch (error: unknown) {
      last = error;
      if (options.signal?.aborted === true) throw error;
      if (!isColdStart(error)) throw error;
      if (now() - started >= budgetMs) throw error;
    }
    await wait(delay, options.signal);
    delay = Math.min(delay + FIRST_DELAY_MS, MAX_DELAY_MS);
    if (now() - started >= budgetMs) throw last;
  }
}
