import { expect, test, type JSHandle, type Locator, type Page, type Route } from "@playwright/test";

/**
 * F-06, in a real browser: can the client say "move this to the root"?
 *
 * The assertion is on the **request body**, not on a tree redrawing. The
 * server has accepted `new_parent_id: null` since M2 and moves the subtree
 * under a row lock; what was broken for eighteen months was the client's
 * inability to express it. A test that checked the resulting tree would pass
 * against a client that sent `new_parent_id: 0`, got a 404, re-read the tree
 * and found it unchanged -- the shape of a test that reaches past the thing it
 * is testing, and this project has found five of those.
 *
 * The API is mocked on purpose rather than for convenience: no database, no
 * cold start, no shared state between runs, and the assertion is exactly the
 * one value that was wrong.
 */

const WHOAMI = { id: 1, email: "guest@guests.invalid", csrf_token: "test-token" };
const ROOTS = {
  items: [
    { id: 1, parent_id: null, name: "projects", type: "folder" },
    // A second root, so the "onto a folder" case can be a real move. Dropping
    // `2026` onto `projects` would be a no-op: the library refuses a drop on a
    // node's existing parent, so no request would be made and the test would
    // fail for a reason that has nothing to do with what it is checking.
    { id: 3, parent_id: null, name: "archive", type: "folder" },
  ],
  next_cursor: null,
};
const CHILDREN_OF_1 = {
  items: [{ id: 2, parent_id: 1, name: "2026", type: "folder" }],
  next_cursor: null,
};

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/**
 * Drive an HTML5 drag.
 *
 * `page.mouse.down/move/up` does **not** work here, and the reason is worth
 * writing down because it cost a CI cycle: `react-dnd-html5-backend` listens
 * for the browser's native `dragstart`/`dragover`/`drop` events, and Chromium
 * only emits those for a genuine user drag. Synthetic mouse movement produces
 * a test that fails with "no request was made", which reads like a broken
 * application rather than a test driving the wrong events.
 *
 * Dispatching the drag events with one shared `DataTransfer` is what the
 * backend is actually listening for.
 */
async function dragOnto(page: Page, source: Locator, target: Locator): Promise<void> {
  const dataTransfer: JSHandle<DataTransfer> = await page.evaluateHandle(() => new DataTransfer());
  await source.dispatchEvent("dragstart", { dataTransfer });
  await target.dispatchEvent("dragenter", { dataTransfer });
  await target.dispatchEvent("dragover", { dataTransfer });
  await target.dispatchEvent("drop", { dataTransfer });
  await source.dispatchEvent("dragend", { dataTransfer });
  await dataTransfer.dispose();
}

interface Captured {
  bodies: unknown[];
  csrf: (string | undefined)[];
}

async function stubApi(page: Page, captured: Captured): Promise<void> {
  await page.route("**/api/v1/auth/me", (r) => json(r, WHOAMI));
  await page.route("**/api/v1/roots*", (r) => json(r, ROOTS));
  await page.route("**/api/v1/nodes/1/children*", (r) => json(r, CHILDREN_OF_1));
  await page.route("**/api/v1/nodes/2/children*", (r) => json(r, { items: [], next_cursor: null }));
  await page.route("**/api/v1/nodes/3/children*", (r) => json(r, { items: [], next_cursor: null }));
  await page.route("**/api/v1/nodes/*/move", async (route) => {
    captured.bodies.push(route.request().postDataJSON());
    captured.csrf.push(route.request().headers()["x-csrf-token"]);
    await json(route, { id: 2, parent_id: null, name: "2026", type: "folder" });
  });
}

test("a nested folder can be dragged out to the root", async ({ page }) => {
  const captured: Captured = { bodies: [], csrf: [] };
  await stubApi(page, captured);

  await page.goto("/");
  await expect(page.getByText("projects")).toBeVisible();

  await page.getByRole("button", { name: "Expand projects" }).click();
  await expect(page.getByText("2026")).toBeVisible();

  // The source is the list item the library made draggable; the target is the
  // tree's own root container, which is the element that represents "no
  // parent". The old client's drop handler looked the target up by id, found
  // nothing for the root, and returned early -- so this gesture did nothing at
  // all, for eighteen months.
  const source = page.locator("li").filter({ hasText: "2026" }).last();
  const root = page.locator(".tree-root").first();
  await dragOnto(page, source, root);

  await expect.poll(() => captured.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0);

  // The whole point: `null`, not `0`, not omitted.
  expect(captured.bodies[0]).toEqual({ new_parent_id: null });

  // And the other half of why the old client could never have worked: it sent
  // no CSRF header at all, so every unsafe request would have been refused
  // even if the body had been right.
  expect(captured.csrf[0]).toBe("test-token");
});

test("dropping a folder onto a folder sends that folder's id, not null", async ({ page }) => {
  // The complement, and the reason the first test is not vacuous: if the
  // client sent `null` for every drop, test one would pass while the
  // application was broken in the opposite direction.
  const captured: Captured = { bodies: [], csrf: [] };
  await stubApi(page, captured);

  await page.goto("/");
  await page.getByRole("button", { name: "Expand projects" }).click();
  await expect(page.getByText("2026")).toBeVisible();

  const source = page.locator("li").filter({ hasText: "2026" }).last();
  const archive = page.locator("li").filter({ hasText: "archive" }).last();
  await dragOnto(page, source, archive);

  await expect.poll(() => captured.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0);
  expect(captured.bodies[0]).toEqual({ new_parent_id: 3 });
});
