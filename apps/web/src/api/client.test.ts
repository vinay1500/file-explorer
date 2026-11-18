import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, getCsrfToken, request, setCsrfToken } from "./client";

/**
 * The client's job is three properties, and each one has a bug behind it.
 */

function reply(status: number, body: unknown, ok = status < 400): Response {
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  setCsrfToken(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
});

describe("relative URLs", () => {
  it("refuses an absolute URL instead of sending it", async () => {
    // F-01's root cause. An absolute URL bypasses the Vercel rewrite, makes
    // the request cross-site, and the SameSite=Lax session cookie is refused
    // at storage time -- so every authenticated call fails and nothing says
    // why. A throw here is louder than a 401 later.
    await expect(request("https://file-explorer-api-xgxr.onrender.com/api/v1/roots")).rejects.toThrow(
      /refusing a non-relative request/,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends same-origin credentials", async () => {
    fetchMock.mockResolvedValue(reply(200, { items: [] }));
    await request("/api/v1/roots");
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ credentials: "same-origin" });
  });
});

describe("the CSRF token", () => {
  it("is not sent on a safe method", async () => {
    setCsrfToken("tok");
    fetchMock.mockResolvedValue(reply(200, {}));
    await request("/api/v1/roots");
    const headers = (fetchMock.mock.calls[0]?.[1] as RequestInit).headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("is sent on an unsafe method", async () => {
    setCsrfToken("tok");
    fetchMock.mockResolvedValue(reply(200, {}));
    await request("/api/v1/nodes/1/move", { method: "POST", body: { new_parent_id: null } });
    const headers = (fetchMock.mock.calls[0]?.[1] as RequestInit).headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("tok");
  });

  it("is cleared by a 401, so no caller has to remember to", async () => {
    setCsrfToken("tok");
    fetchMock.mockResolvedValue(reply(401, { title: "Unauthorized" }, false));
    await expect(request("/api/v1/roots")).rejects.toBeInstanceOf(ApiError);
    expect(getCsrfToken()).toBeNull();
  });
});

describe("a 403 on an unsafe method", () => {
  it("refreshes the token and retries exactly once", async () => {
    setCsrfToken("stale");
    fetchMock
      .mockResolvedValueOnce(reply(403, { code: "CSRF_FAILED" }, false))
      .mockResolvedValueOnce(reply(200, { csrf_token: "fresh" }))
      .mockResolvedValueOnce(reply(200, { id: 1 }));

    await expect(request("/api/v1/nodes/1/move", { method: "POST", body: {} })).resolves.toEqual({
      id: 1,
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(getCsrfToken()).toBe("fresh");
  });

  it("gives up rather than looping when the retry also fails", async () => {
    // The property that matters is the *count*. A loop here turns one stale
    // token into an unbounded request storm against the endpoints with the
    // tightest rate limit in the API, from a client that believes it is being
    // helpful.
    setCsrfToken("stale");
    fetchMock
      .mockResolvedValueOnce(reply(403, { code: "CSRF_FAILED" }, false))
      .mockResolvedValueOnce(reply(200, { csrf_token: "fresh" }))
      .mockResolvedValueOnce(reply(403, { code: "CSRF_FAILED" }, false));

    await expect(request("/api/v1/nodes/1/move", { method: "POST", body: {} })).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("does not retry a safe method", async () => {
    fetchMock.mockResolvedValue(reply(403, { code: "FORBIDDEN" }, false));
    await expect(request("/api/v1/roots")).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("failures carry the problem document", () => {
  it("exposes the code so a caller can branch on it", async () => {
    fetchMock.mockResolvedValue(reply(409, { code: "CYCLE_DETECTED", title: "Conflict" }, false));
    await expect(request("/api/v1/nodes/1/move", { method: "POST", body: {} })).rejects.toMatchObject(
      { status: 409, code: "CYCLE_DETECTED" },
    );
  });

  it("survives a body that is not a problem document", async () => {
    // No cast: `fetchMock` is `vi.fn()`, so it accepts this shape as-is, and
    // asserting a type the receiver already accepts is noise the linter is
    // right to reject.
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      json: () => Promise.reject(new Error("not json")),
    });
    await expect(request("/api/v1/roots")).rejects.toMatchObject({ status: 502, problem: null });
  });
});
