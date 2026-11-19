import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, getCsrfToken, setCsrfToken } from "./client";
import { register, signIn, signInAsGuest, signOut, whoAmI } from "./session";

/**
 * What each call actually sends, and what it does with the token that comes
 * back.
 *
 * This file exists because Gate 3 proved the suite could not see either.
 * `AuthPanel.test.tsx` mocks this whole module, so **changing the login path
 * to a typo left 46 of 46 tests green**, and so did deleting the line that
 * captures the CSRF token — which would have produced a session that cannot
 * perform a single write, with every test passing.
 *
 * Nothing here is mocked except `fetch`.
 */

function reply(status: number, body: unknown, ok = status < 400): Response {
  return { ok, status, json: () => Promise.resolve(body) } as unknown as Response;
}

const SESSION = { csrf_token: "a-token", expires_at: "2026-12-01T00:00:00Z" };

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

function callOf(index = 0): { path: string; init: RequestInit } {
  const [path, init] = fetchMock.mock.calls[index] as [string, RequestInit];
  return { path, init };
}

describe("signIn", () => {
  it("posts the credentials to the login route", async () => {
    fetchMock.mockResolvedValue(reply(200, SESSION));
    await signIn("person@example.com", "a long enough password");

    const { path, init } = callOf();
    expect(path).toBe("/api/v1/auth/login");
    expect(init.method).toBe("POST");
    // `BodyInit` is a union wide enough that the linter will not let it near
    // `String()`; the client only ever sends a JSON string.
    expect(JSON.parse(init.body as string)).toEqual({
      email: "person@example.com",
      password: "a long enough password",
    });
  });

  it("adopts the CSRF token, without which no write can succeed", async () => {
    fetchMock.mockResolvedValue(reply(200, SESSION));
    await signIn("person@example.com", "a long enough password");
    expect(getCsrfToken()).toBe("a-token");
  });

  it("lets a 401 through as an ApiError rather than swallowing it", async () => {
    fetchMock.mockResolvedValue(reply(401, { title: "no" }, false));
    await expect(signIn("a@b.co", "a long enough password")).rejects.toBeInstanceOf(ApiError);
    expect(getCsrfToken()).toBeNull();
  });
});

describe("register", () => {
  it("posts to the register route and nowhere else", async () => {
    fetchMock.mockResolvedValue(reply(202, { accepted: true }));
    await register("person@example.com", "a long enough password");

    expect(fetchMock).toHaveBeenCalledOnce();
    const { path, init } = callOf();
    expect(path).toBe("/api/v1/auth/register");
    expect(init.method).toBe("POST");
  });

  it("does not sign anyone in: a 202 carries no session", async () => {
    fetchMock.mockResolvedValue(reply(202, { accepted: true }));
    await register("person@example.com", "a long enough password");
    expect(getCsrfToken()).toBeNull();
  });
});

describe("signInAsGuest", () => {
  it("posts to the guest route and adopts its token", async () => {
    fetchMock.mockResolvedValue(reply(200, SESSION));
    await signInAsGuest();

    const { path, init } = callOf();
    expect(path).toBe("/api/v1/auth/guest");
    expect(init.method).toBe("POST");
    expect(getCsrfToken()).toBe("a-token");
  });
});

describe("signOut", () => {
  it("drops the token even when the request fails", async () => {
    setCsrfToken("a-token");
    fetchMock.mockResolvedValue(reply(401, null, false));

    await expect(signOut()).rejects.toBeInstanceOf(ApiError);

    // If logout 401s the session was already gone; holding the token is
    // strictly worse than dropping it.
    expect(getCsrfToken()).toBeNull();
  });
});

describe("whoAmI", () => {
  it("returns null for 401, because being signed out is not an error", async () => {
    fetchMock.mockResolvedValue(reply(401, null, false));
    await expect(whoAmI()).resolves.toBeNull();
  });

  it("still throws for 503, which is not the same as being signed out", async () => {
    fetchMock.mockResolvedValue(reply(503, { title: "degraded" }, false));
    await expect(whoAmI()).rejects.toBeInstanceOf(ApiError);
  });
});
