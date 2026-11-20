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

/**
 * A per-attempt ceiling, so one hung connection cannot eat the whole budget.
 *
 * **This number was 15 seconds, and that made the whole loop incapable of
 * succeeding.** A cold response from this service takes about 38 seconds
 * (measured 19 November: `curl` direct, `200` in 38.6s, with the two that
 * followed in 0.3s and 0.2s). Every attempt was therefore aborted twenty-three
 * seconds before the answer arrived, and no amount of retrying could change
 * that: ten attempts of 15 seconds plus backoff is 184 seconds, which is
 * exactly the "I waited 181 seconds" Vinay was shown, twice.
 *
 * > **A per-attempt timeout shorter than the operation it is waiting for turns
 * > a retry loop into a guarantee of failure.** The 15 was an assumption about
 * > how long a cold start takes. 38.6 is a measurement.
 *
 * 75 seconds gives that measurement roughly double the headroom. The thing it
 * still protects against is a connection that hangs for ever, which now costs
 * a third of the budget rather than the whole of it.
 */
const ATTEMPT_TIMEOUT_MS = 75_000;

/**
 * The API's own origin, and the only absolute URL in this application.
 *
 * **This module has now been wrong three times, and the third one was not in
 * this loop at all.** The loop was correct. It was sending its requests down a
 * path that cannot wake the service.
 *
 * Measured 20 November, with the instance left alone for sixteen minutes and
 * nothing else touched:
 *
 * | request | result |
 * | --- | --- |
 * | direct to this origin | held open, then `200` in **32.5s** |
 * | through the Vercel rewrite | **`502` twelve times in 87 seconds**, every one `x-render-routing: no-deploy`, and the instance still asleep afterwards |
 *
 * So a browser, which only ever speaks to the rewrite, could retry for any
 * budget at any ceiling and never wake anything. M8 raised a retry, M12 fixed
 * what it waited on, M13 fixed the arithmetic -- three fixes to a mechanism
 * that was already doing its job.
 *
 * `vercel.json` holds the same hostname as its rewrite destination, and
 * `test_deploy_config.py` fails if the two ever disagree, because a wake ping
 * aimed at a host this project no longer deploys to would fail silently and
 * look exactly like the bug above.
 */
export const WAKE_ORIGIN = "https://file-explorer-api-xgxr.onrender.com";

/**
 * Knock on the API's own door, so that it starts.
 *
 * Cross-origin and deliberately `no-cors`: there is nothing to read. The point
 * is only that the request *arrives*, because an arriving request is what makes
 * Render boot the container. An opaque response carries no status and no body,
 * so this cannot report success and does not try to -- the caller goes on
 * retrying the same-origin request whose answer it actually needs, and that
 * request starts succeeding once the container is up.
 *
 * **Nothing about D-22 changes.** Every request that carries or needs the
 * session still goes through `client.ts`, still relative, still same-origin,
 * still `SameSite=Lax`. This one sends no cookie (`credentials: "omit"`),
 * receives nothing, and exists purely as a knock. It is also why `client.ts`
 * keeps its throw on absolute URLs untouched: this is the single deliberate
 * exception and it lives outside the client on purpose.
 */
export async function nudgeOrigin(signal?: AbortSignal): Promise<void> {
  // `exactOptionalPropertyTypes` is on, and `RequestInit.signal` is
  // `AbortSignal | null` rather than optional -- so the absent case has to be
  // `null`, not an omitted key holding `undefined`.
  const init: RequestInit = {
    mode: "no-cors",
    credentials: "omit",
    cache: "no-store",
    signal: signal ?? null,
  };
  try {
    await fetch(`${WAKE_ORIGIN}/api/v1/health`, init);
  } catch {
    // An opaque request that fails tells us nothing a retry of the real
    // request will not tell us better. Swallowing it here is not hiding an
    // error; there is no error to hide, because there was never a response
    // to read.
  }
}


/**
 * Total time to keep trying.
 *
 * Render's documentation says "up to a minute"; the measured cold response is
 * about 38 seconds once a request is actually allowed to finish. The budget
 * has to hold at least two or three of those, because a browser's first
 * attempt can land while the container is still being scheduled rather than
 * merely booting.
 */
export const WAKE_BUDGET_MS = 240_000;

const FIRST_DELAY_MS = 1_000;
const MAX_DELAY_MS = 4_000;

export interface WakeState {
  /** How many requests have been made, including the one in flight. */
  readonly attempt: number;
  /** Milliseconds since the first attempt. */
  readonly elapsedMs: number;
  /**
   * The ceiling this attempt is running under.
   *
   * Reported rather than inferred, so a test can hold the number the loop
   * *applies* against a measured cold start. An earlier version of that test
   * asserted a getter that returned the constant, and a mutation which
   * hard-coded a different value at the call site sailed past it.
   */
  readonly ceilingMs: number;
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
  /**
   * Override the wake knock. Only tests pass this, so that "did it knock, and
   * how many times" can be asserted without a network.
   */
  nudge?: (signal?: AbortSignal) => Promise<void>;
  /**
   * Override the per-attempt ceiling. Only tests pass this: it is what lets
   * the relationship between the ceiling and a slow operation be exercised at
   * millisecond scale, with real timers, rather than asserted about constants.
   */
  attemptTimeoutMs?: number;
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

  const ceilingMs = options.attemptTimeoutMs ?? ATTEMPT_TIMEOUT_MS;
  const nudge = options.nudge ?? nudgeOrigin;

  // At most one knock in flight. Render holds the first one open for the whole
  // cold start, so stacking them would add load and tell us nothing new -- and
  // the loop below is retrying every one to four seconds, which would be
  // twenty-odd simultaneous requests at a service that is trying to boot.
  let knocking: Promise<void> | null = null;
  const knockOnce = (): void => {
    if (knocking !== null) return;
    knocking = nudge(options.signal).finally(() => {
      knocking = null;
    });
  };

  for (;;) {
    attempts += 1;
    onState?.({ attempt: attempts, elapsedMs: now() - started, ceilingMs });
    try {
      return await attempt(AbortSignal.timeout(ceilingMs));
    } catch (error: unknown) {
      last = error;
      if (options.signal?.aborted === true) throw error;
      if (!isColdStart(error)) throw error;
      // Only once a cold start is *confirmed*. A warm service answers the
      // first attempt and is never knocked on at all.
      knockOnce();
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
