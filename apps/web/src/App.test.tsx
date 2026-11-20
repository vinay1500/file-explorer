import "@testing-library/jest-dom/vitest";
import { StrictMode } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

/**
 * The shell, rendered the way `main.tsx` renders it: inside `StrictMode`.
 *
 * That detail is the whole point of this file. **React double-invokes effects
 * under StrictMode in development**, so the session resolve runs twice, and
 * for four hours this component answered the second 502 by replacing the
 * waiting panel with `request failed with 502 (HTTP 502)` — the exact string
 * M8 exists to delete — while the first wake kept polling behind it.
 *
 * The browser suite cannot catch it: Playwright serves `vite preview`, a
 * production build, where StrictMode does not double-invoke. Found by Gate 3.
 */

function reply(status: number, body: unknown, ok = status < 400): Response {
  return { ok, status, json: () => Promise.resolve(body) } as unknown as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("a cold start under StrictMode", () => {
  it("shows the waiting panel and never the raw 502", async () => {
    fetchMock.mockResolvedValue(reply(502, null, false));

    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );

    // The first attempt gets no panel: on a warm instance it answers in a
    // third of a second, and a flash of "waking the server" would be a lie.
    // The panel appears on the first retry, one second later.
    await waitFor(
      () => {
        expect(screen.getByText(/Waking the server/)).toBeInTheDocument();
      },
      { timeout: 4_000 },
    );

    // The second effect invocation must not be able to speak over the first.
    expect(screen.queryByText(/HTTP 502/)).not.toBeInTheDocument();
    expect(screen.queryByText(/request failed with/)).not.toBeInTheDocument();
  });

  it("keeps waiting when the session check itself 502s, over and over", async () => {
    // **This is the bug Vinay hit on 19 November**, in the shape the code
    // could actually produce it: the old version polled `/api/v1/health`
    // until it answered and then made ONE unprotected call to
    // `/api/v1/auth/me`. When that call 502'd, the waiting panel was replaced
    // by `request failed with 502 (HTTP 502)` after 51 seconds of waiting.
    //
    // There is no health poll any more, so the test is simply: the request
    // the page needs fails five times and then works, and the screen never
    // stops waiting.
    fetchMock
      .mockResolvedValueOnce(reply(502, null, false))
      .mockResolvedValueOnce(reply(502, null, false))
      .mockResolvedValueOnce(reply(502, null, false))
      .mockResolvedValueOnce(reply(502, null, false))
      .mockResolvedValueOnce(reply(502, null, false))
      .mockResolvedValue(reply(401, null, false));

    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );

    await waitFor(
      () => {
        expect(screen.getByText(/Waking the server/)).toBeInTheDocument();
      },
      { timeout: 4_000 },
    );
    expect(screen.queryByText(/request failed with/)).not.toBeInTheDocument();

    // 401 means "nobody is signed in", which is the ordinary start-up state:
    // the gate appears, not an error.
    await waitFor(
      () => {
        expect(screen.getByText("Have a look around")).toBeInTheDocument();
      },
      { timeout: 25_000 },
    );
    expect(screen.queryByText(/request failed with/)).not.toBeInTheDocument();
    expect(screen.queryByText(/HTTP 502/)).not.toBeInTheDocument();
  }, 30_000);

  it("stops saying \"checking\" within a few seconds, even on the first attempt", async () => {
    // The waiting panel used to appear only from the second attempt. With a
    // 75-second ceiling that left a cold visitor watching "Checking whether
    // you're already signed in" for over a minute with nothing explaining it.
    // A warm instance answers in about a third of a second, so this threshold
    // costs a warm visitor nothing.
    // Never settles: the first attempt is simply slow, which is exactly what a
    // cold start looks like from the browser's side.
    fetchMock.mockReturnValue(new Promise<Response>(() => undefined));

    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );

    expect(screen.getByText(/Checking whether/)).toBeInTheDocument();

    await waitFor(
      () => {
        expect(screen.getByText(/Waking the server/)).toBeInTheDocument();
      },
      { timeout: 6_000 },
    );
  }, 10_000);

  it("reports a 503 immediately instead of waiting it out", async () => {
    fetchMock.mockResolvedValue(reply(503, { title: "degraded" }, false));

    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/can't reach its database/);
    });
    expect(screen.queryByText(/Waking the server/)).not.toBeInTheDocument();
  });

  it("opens light, whatever the machine prefers", async () => {
    fetchMock.mockResolvedValue(reply(401, null, false));
    vi.stubGlobal("matchMedia", () => ({ matches: true, media: "(prefers-color-scheme: dark)" }));

    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );

    await waitFor(() => {
      expect(document.documentElement.dataset.theme).toBe("light");
    });
    expect(screen.getByRole("button", { name: /switch to dark/i })).toBeInTheDocument();
  });
});
