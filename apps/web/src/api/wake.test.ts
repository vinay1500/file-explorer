import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { StillWaking, isColdStart, keepTrying, wakeApi } from "./wake";

/**
 * The cold start, which is the first thing a visitor meets and was the first
 * thing the client got wrong.
 *
 * A free Render instance sleeps after fifteen idle minutes and its edge
 * answers **502** until the container is up. The first client turned that into
 * `request failed with 502 (HTTP 502)` after one attempt. These tests are
 * about the two distinctions that make waiting correct rather than stubborn:
 * which failures mean "not yet", and which mean "stop".
 */

function reply(status: number, body: unknown, ok = status < 400): Response {
  return { ok, status, json: () => Promise.resolve(body) } as unknown as Response;
}

const UP = reply(200, { status: "ok", db: "ok" });

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** Let the poll's `setTimeout` waits elapse without real time passing. */
async function settle(promise: Promise<unknown>): Promise<unknown> {
  const result = promise.then(
    (value) => ({ ok: true, value }) as const,
    (error: unknown) => ({ ok: false, error }) as const,
  );
  await vi.runAllTimersAsync();
  return result;
}

describe("isColdStart", () => {
  it("counts 502 and 504, which are what an edge says while a container starts", () => {
    expect(isColdStart(new ApiError(502, null))).toBe(true);
    expect(isColdStart(new ApiError(504, null))).toBe(true);
  });

  it("counts a thrown fetch, because nothing answered at all", () => {
    expect(isColdStart(new TypeError("Failed to fetch"))).toBe(true);
    expect(isColdStart(new DOMException("timed out", "TimeoutError"))).toBe(true);
  });

  it("does NOT count 503", () => {
    // `/api/v1/health` answers 503 itself when the process is up and the
    // database is unreachable. Waiting does not fix it -- and it is the exact
    // condition the April 2025 deployment sat in for seventeen months, so
    // retrying it would hide the one failure this project exists to remember.
    expect(isColdStart(new ApiError(503, null))).toBe(false);
  });

  it("does NOT count 500, which means the application ran and failed", () => {
    expect(isColdStart(new ApiError(500, null))).toBe(false);
  });
});

describe("wakeApi", () => {
  it("keeps polling through 502s and resolves when the service answers", async () => {
    fetchMock
      .mockResolvedValueOnce(reply(502, null, false))
      .mockResolvedValueOnce(reply(502, null, false))
      .mockResolvedValueOnce(UP);

    const result = (await settle(wakeApi())) as { ok: boolean; value: unknown };

    expect(result.ok).toBe(true);
    expect(result.value).toEqual({ status: "ok", db: "ok" });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("only ever asks for the health endpoint, with GET", async () => {
    fetchMock.mockResolvedValue(UP);
    await settle(wakeApi());

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/v1/health");
    // A POST that returns 502 may or may not have reached the application, so
    // retrying the visitor's own request could leave two guest accounts
    // behind. The wake is a GET against a route that changes nothing.
    expect(init.method ?? "GET").toBe("GET");
  });

  it("stops at once on 503 rather than waiting out the budget", async () => {
    fetchMock.mockResolvedValue(reply(503, { status: "degraded", db: "unreachable" }, false));

    const result = (await settle(wakeApi())) as { ok: boolean; error: unknown };

    expect(result.ok).toBe(false);
    expect(result.error).toBeInstanceOf(ApiError);
    expect((result.error as ApiError).status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("gives up when the budget runs out, and says so in its own type", async () => {
    fetchMock.mockResolvedValue(reply(502, null, false));

    const result = (await settle(wakeApi(undefined, { budgetMs: 10_000 }))) as {
      ok: boolean;
      error: unknown;
    };

    expect(result.ok).toBe(false);
    // **Not an ApiError.** Whatever renders a failure must be able to tell
    // "still starting" from "answered something", because `request failed
    // with 502` reached the screen once already, on this very path.
    expect(result.error).toBeInstanceOf(StillWaking);
    expect((result.error as StillWaking).last).toBeInstanceOf(ApiError);
    expect(((result.error as StillWaking).last as ApiError).status).toBe(502);
    // Bounded: 1s, 2s, 3s, 4s between attempts against a ten-second budget.
    // The exact count matters less than that it is small and finite -- an
    // unbounded poll against a sleeping instance is a request storm.
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(6);
    expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
  });

  it("reports each attempt so the page can show the wait", async () => {
    fetchMock.mockResolvedValueOnce(reply(502, null, false)).mockResolvedValueOnce(UP);
    const seen: number[] = [];

    await settle(wakeApi((state) => seen.push(state.attempt)));

    expect(seen).toEqual([1, 2]);
  });
});

describe("keepTrying, which is what the page actually uses", () => {
  it("retries the caller's own operation rather than a cheaper proxy for it", async () => {
    // The defect this replaces: poll `/api/v1/health` until it answers, then
    // make ONE unprotected call to the thing the page needs. Health is
    // `SELECT 1` and exempt from the limiter; the first real request builds a
    // connection pool. A starting container serves one and not the other, and
    // the screen printed `request failed with 502` after 51 seconds of saying
    // "waking".
    let calls = 0;
    const operation = vi.fn(() => {
      calls += 1;
      return calls < 4
        ? Promise.reject(new ApiError(502, null))
        : Promise.resolve("the real answer");
    });

    const result = (await settle(keepTrying(operation))) as { ok: boolean; value: unknown };

    expect(result.ok).toBe(true);
    expect(result.value).toBe("the real answer");
    expect(operation).toHaveBeenCalledTimes(4);
  });

  it("hands each attempt a signal, so one hung call cannot eat the budget", async () => {
    const seen: AbortSignal[] = [];
    await settle(
      keepTrying((signal) => {
        seen.push(signal);
        return Promise.resolve("ok");
      }),
    );
    expect(seen).toHaveLength(1);
    expect(seen[0]).toBeInstanceOf(AbortSignal);
  });

  it("rethrows anything that is not a cold start, untouched", async () => {
    const boom = new ApiError(500, null);
    const result = (await settle(
      keepTrying(() => Promise.reject(boom)),
    )) as { ok: boolean; error: unknown };

    expect(result.ok).toBe(false);
    expect(result.error).toBe(boom);
  });
});

describe("the per-attempt ceiling, against a measured cold start", () => {
  // Measured on the deployed service, 19 November: a cold response took
  // **38.6 seconds**, and the two requests after it took 0.3s and 0.2s.
  const MEASURED_COLD_RESPONSE_MS = 38_600;

  it("is longer than a cold response, or the loop can never succeed", async () => {
    // The ceiling was 15 seconds. Every attempt was therefore aborted
    // twenty-three seconds before the answer arrived, and ten attempts of
    // that is 184 seconds -- exactly the "I waited 181 seconds" the page
    // showed Vinay, twice. The budget was not the problem; raising it to 180
    // only made the same failure arrive later.
    //
    // This test exists so that number can never again be set from an
    // assumption about how long a cold start takes.
    // Read off the loop itself rather than from a getter: the first version of
    // this asserted a function that returned the constant, and a mutation
    // which hard-coded 15 seconds at the call site went straight past it.
    let ceiling = 0;
    fetchMock.mockResolvedValue(UP);
    await settle(
      wakeApi((state) => {
        ceiling = state.ceilingMs;
      }),
    );

    expect(
      ceiling,
      `the per-attempt ceiling the loop applies is ${String(ceiling)}ms and a cold response ` +
        `takes ${String(MEASURED_COLD_RESPONSE_MS)}ms, so no attempt could finish`,
    ).toBeGreaterThan(MEASURED_COLD_RESPONSE_MS);
  });

  it("lets a slow attempt finish when the ceiling is above it, and never when it is below", async () => {
    // Real timers at millisecond scale, because `AbortSignal.timeout` is a
    // platform API and vitest's fake clock does not drive it -- the first
    // version of this test used fake timers, so the abort never fired and it
    // passed against the 15-second ceiling it was written to catch. A test
    // that cannot see the thing it names is worse than no test.
    vi.useRealTimers();

    const slow = (ms: number) => (signal: AbortSignal) =>
      new Promise<string>((resolve, reject) => {
        const timer = setTimeout(() => {
          resolve("awake");
        }, ms);
        signal.addEventListener("abort", () => {
          clearTimeout(timer);
          reject(new DOMException("timed out", "TimeoutError"));
        });
      });

    // Ceiling above the operation: one attempt, and it completes.
    await expect(
      keepTrying(slow(60), undefined, { attemptTimeoutMs: 300, budgetMs: 2_000 }),
    ).resolves.toBe("awake");

    // Ceiling below the operation: **no budget can rescue this.** Every
    // attempt is cut off before the answer arrives, which is precisely what
    // happened in production, ten times in a row, for 181 seconds.
    await expect(
      keepTrying(slow(400), undefined, { attemptTimeoutMs: 50, budgetMs: 600 }),
    ).rejects.toBeInstanceOf(StillWaking);
  }, 10_000);
});
