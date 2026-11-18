import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * F-06, end to end: can the client say "move this to the root"?
 *
 * The assertion is on the **request body**, not on a tree redrawing. The
 * server has accepted `new_parent_id: null` since M2 and moves the subtree
 * under a row lock; what was broken for eighteen months was the client's
 * inability to express it. A test that checked the resulting tree would pass
 * against a client that sent `new_parent_id: 0` and got a 404, because the
 * tree would be re-read and look unchanged -- which is the shape of a test
 * that reaches past the thing it is testing.
 */

const SESSION = { csrf_token: "test-token", expires_at: "2099-01-01T00:00:00Z" };
const WHOAMI = { id: 1, email: "guest@guests.invalid", csrf_token: "test-token" };

/** A root folder with one folder inside it. */
const ROOTS = { items: [{ id: 1, parent_id: null, name: "projects", type: "folder" }], next_cursor: null };
const CHILDREN_OF_1 = {
  items: [{ id: 2, parent_id: 1, name: "2026", type: "folder" }],
  next_cursor: null,
};

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function stubApi(page: Page, onMove: (body: unknown) => void): Promise<void> {
  await page.route("**/api/v1/auth/me", (r) => json(r, WHOAMI));
  await page.route("**/api/v1/auth/guest", (r) => json(r, SESSION));
  await page.route("**/api/v1/roots*", (r) => json(r, ROOTS));
  await page.route("**/api/v1/nodes/1/children*", (r) => json(r, CHILDREN_OF_1));
  await page.route("**/api/v1/nodes/2/children*", (r) => json(r, { items: [], next_cursor: null }));
  await page.route("**/api/v1/nodes/*/move", async (route) => {
    onMove(route.request().postDataJSON());
    await json(route, { id: 2, parent_id: null, name: "2026", type: "folder" });
  });
}

test("a nested folder can be dragged out to the root", async ({ page }) => {
  const moves: unknown[] = [];
  await stubApi(page, (body) => moves.push(body));

  await page.goto("/");
  await expect(page.getByText("projects")).toBeVisible();

  // Open the root folder so its child is on screen.
  await page.getByRole("button", { name: "Expand projects" }).click();
  const child = page.getByText("2026");
  await expect(child).toBeVisible();

  // Drag the child onto the tree's own root container -- the empty area that
  // represents "no parent". This is the gesture the old client could not
  // complete: its drop handler looked the target up by id, found nothing for
  // the root, and returned early.
  await child.hover();
  await page.mouse.down();
  const root = page.locator(".tree-root");
  const box = await root.boundingBox();
  if (box === null) throw new Error("the tree root has no box to drop onto");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height - 4, { steps: 12 });
  await page.mouse.up();

  await expect.poll(() => moves.length, { timeout: 5_000 }).toBeGreaterThan(0);

  // The whole point. `null`, not `0`, not omitted.
  expect(moves[0]).toEqual({ new_parent_id: null });
});

test("the move request carries the CSRF header", async ({ page }) => {
  // The other half of why the old client could never have worked: it sent no
  // CSRF header at all, so every unsafe request would have been refused.
  let header: string | null | undefined;
  await page.route("**/api/v1/auth/me", (r) => json(r, WHOAMI));
  await page.route("**/api/v1/roots*", (r) => json(r, ROOTS));
  await page.route("**/api/v1/nodes/1/children*", (r) => json(r, CHILDREN_OF_1));
  await page.route("**/api/v1/nodes/*/move", async (route) => {
    header = route.request().headers()["x-csrf-token"];
    await json(route, { id: 2, parent_id: null });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Expand projects" }).click();
  const child = page.getByText("2026");
  await expect(child).toBeVisible();

  await child.hover();
  await page.mouse.down();
  const box = await page.locator(".tree-root").boundingBox();
  if (box === null) throw new Error("the tree root has no box to drop onto");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height - 4, { steps: 12 });
  await page.mouse.up();

  await expect.poll(() => header, { timeout: 5_000 }).toBe("test-token");
});
