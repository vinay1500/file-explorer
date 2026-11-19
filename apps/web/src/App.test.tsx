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

    await waitFor(() => {
      expect(screen.getByText(/Waking the server/)).toBeInTheDocument();
    });

    // The second effect invocation must not be able to speak over the first.
    expect(screen.queryByText(/HTTP 502/)).not.toBeInTheDocument();
    expect(screen.queryByText("That did not work")).not.toBeInTheDocument();
  });

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
