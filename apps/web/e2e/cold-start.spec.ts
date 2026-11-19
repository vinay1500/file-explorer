import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * What a visitor sees when the free instance is asleep.
 *
 * This is the first impression of the whole project, and until M8 it was
 * `request failed with 502 (HTTP 502)` half a second after the page loaded.
 * Render stops a free service after fifteen idle minutes; the next request
 * reaches the edge, the edge starts the container, and **the edge answers 502
 * until it is up**. Nothing was broken and nothing was waiting.
 *
 * The two tests here are the two halves of being right about that: wait when
 * waiting will work, and stop when it will not.
 */

const WHOAMI = { id: 1, email: "guest@guests.invalid", csrf_token: "test-token" };
const ROOTS = {
  items: [{ id: 1, parent_id: null, name: "projects", type: "folder" }],
  next_cursor: null,
};

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/** A sleeping instance: the edge's 502, with no body worth reading. */
async function edge502(route: Route): Promise<void> {
  await route.fulfill({ status: 502, contentType: "text/html", body: "<html>Bad gateway</html>" });
}

async function countedRoutes(page: Page, asleepFor: number): Promise<{ calls: number }> {
  const state = { calls: 0 };
  await page.route("**/api/v1/**", async (route) => {
    state.calls += 1;
    if (state.calls <= asleepFor) {
      await edge502(route);
      return;
    }
    const url = route.request().url();
    if (url.includes("/auth/me")) return json(route, WHOAMI);
    if (url.includes("/health")) return json(route, { status: "ok", db: "ok" });
    if (url.includes("/roots")) return json(route, ROOTS);
    return json(route, { items: [], next_cursor: null });
  });
  return state;
}

test("a sleeping instance is waited for, not reported as a failure", async ({ page }) => {
  // Three 502s, then the container is up: `/auth/me`, then the health poll
  // twice, then everything answers.
  await countedRoutes(page, 3);
  await page.goto("/");

  // The thing that was missing: an explanation, while it is happening.
  await expect(page.getByText("Waking the server")).toBeVisible();
  await expect(page.getByText(/sleeps after fifteen idle minutes/)).toBeVisible();
  // And never the raw status line.
  await expect(page.getByText(/HTTP 502/)).toHaveCount(0);

  // Then it comes up on its own, with no second click.
  await expect(page.getByText("projects")).toBeVisible({ timeout: 20_000 });
});

test("a database that is unreachable is reported at once, not waited out", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    await json(route, { status: "degraded", db: "unreachable" }, 503);
  });
  await page.goto("/");

  // 503 from the health route means the process is up and the database is
  // gone -- the condition the April 2025 deployment sat in for seventeen
  // months. Waiting ninety seconds for it would turn a diagnosis into a hang.
  await expect(page.getByText(/cannot reach its database/)).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("Waking the server")).toHaveCount(0);
});
