import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { isColdStart, wakeApi } from "./wake";

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

  it("gives up when the budget runs out, and throws the last failure", async () => {
    fetchMock.mockResolvedValue(reply(502, null, false));

    const result = (await settle(wakeApi(undefined, { budgetMs: 10_000 }))) as {
      ok: boolean;
      error: unknown;
    };

    expect(result.ok).toBe(false);
    expect((result.error as ApiError).status).toBe(502);
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
