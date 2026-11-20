import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * The breadcrumb.
 *
 * Vinay asked for it in those words: *"can we add at the top which specify a
 * windows like feature where we are in the tree, that is if we are inside
 * some folder or clicked some folder it will show like documents/file1."*
 *
 * The API has answered this since M3a at `GET /nodes/{id}/path` -- a recursive
 * CTE that filters by owner in its recursive term as well as its anchor. The
 * page does not call it, and that is deliberate rather than an oversight: a
 * row can only be clicked after its ancestors have been expanded, so every
 * ancestor is already loaded and a request would add a round trip to a click
 * for an answer already in memory. These tests pin the behaviour the endpoint
 * would have given, so the two cannot drift apart unnoticed.
 */

const WHOAMI = { id: 1, email: "guest@guests.invalid", csrf_token: "test-token" };
const ROOTS = {
  items: [
    { id: 1, parent_id: null, name: "Documents", type: "folder" },
    { id: 7, parent_id: null, name: "Photos", type: "folder" },
  ],
  next_cursor: null,
};
const CHILDREN_OF_1 = {
  items: [{ id: 2, parent_id: 1, name: "Invoices", type: "folder" }],
  next_cursor: null,
};
const CHILDREN_OF_2 = {
  items: [{ id: 3, parent_id: 2, name: "march.pdf", type: "file" }],
  next_cursor: null,
};

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function stubApi(page: Page): Promise<void> {
  await page.route("**/api/v1/health", (r) => json(r, { status: "ok", db: "ok" }));
  await page.route("**/api/v1/auth/me", (r) => json(r, WHOAMI));
  await page.route("**/api/v1/roots*", (r) => json(r, ROOTS));
  await page.route("**/api/v1/nodes/1/children*", (r) => json(r, CHILDREN_OF_1));
  await page.route("**/api/v1/nodes/2/children*", (r) => json(r, CHILDREN_OF_2));
  await page.route("**/api/v1/nodes/7/children*", (r) => json(r, { items: [], next_cursor: null }));
}

test("it says the top level before anything is clicked", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  await expect(page.getByText("Documents")).toBeVisible();

  const bar = page.getByRole("navigation", { name: "Where you are" });
  await expect(bar).toBeVisible();
  await expect(bar).toContainText("Top level");
});

test("clicking a nested file shows the whole path down to it", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Expand Documents" }).click();
  await page.getByRole("button", { name: "Expand Invoices" }).click();
  await expect(page.getByText("march.pdf")).toBeVisible();

  await page.getByText("march.pdf").click();

  const bar = page.getByRole("navigation", { name: "Where you are" });
  // Root first, the node itself last -- the order the endpoint returns.
  await expect(bar).toContainText("Documents");
  await expect(bar).toContainText("Invoices");
  await expect(bar).toContainText("march.pdf");
  // The last crumb is the thing you clicked, and it is marked as the location.
  await expect(bar.locator('[aria-current="location"]')).toHaveText("march.pdf");
});

test("it does not pick up a sibling branch", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Expand Documents" }).click();
  await expect(page.getByText("Invoices")).toBeVisible();

  await page.getByText("Invoices").click();

  const bar = page.getByRole("navigation", { name: "Where you are" });
  await expect(bar).toContainText("Documents");
  await expect(bar).not.toContainText("Photos");
});

test("clicking a crumb moves the marker back up the path", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Expand Documents" }).click();
  await page.getByRole("button", { name: "Expand Invoices" }).click();
  await page.getByText("march.pdf").click();

  const bar = page.getByRole("navigation", { name: "Where you are" });
  await bar.getByRole("button", { name: "Documents" }).click();

  await expect(bar.locator('[aria-current="location"]')).toHaveText("Documents");
  await expect(bar).not.toContainText("march.pdf");
});

test("Top level clears it", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Expand Documents" }).click();
  await page.getByText("Invoices").click();

  const bar = page.getByRole("navigation", { name: "Where you are" });
  await bar.getByRole("button", { name: "Top level" }).click();

  await expect(bar.locator('[aria-current="location"]')).toHaveText("Top level");
  await expect(bar).not.toContainText("Invoices");
});

test("the bar is visible, not merely in the DOM", async ({ page }) => {
  // The lesson of F-62 and F-73, applied before it can happen a third time:
  // a control is not delivered because a test can reach it by name.
  await stubApi(page);
  await page.goto("/");
  await expect(page.getByText("Documents")).toBeVisible();

  const bar = page.getByRole("navigation", { name: "Where you are" });
  const box = await bar.boundingBox();
  expect(box).not.toBeNull();
  expect(box?.height ?? 0).toBeGreaterThan(14);
  expect(await bar.evaluate((el) => Number(getComputedStyle(el).opacity))).toBeGreaterThan(0.9);
  // And it is above the tree, which is what "at the top" meant.
  const tree = page.locator(".tree-root");
  const treeBox = await tree.boundingBox();
  expect(box?.y ?? 0).toBeLessThan(treeBox?.y ?? 0);
});

test("clicking a row still renames, and still creates, with the marker on it", async ({ page }) => {
  // The marker is set on pointerdown over the whole row. If that swallowed
  // the controls inside the row, the breadcrumb would have cost two features.
  await stubApi(page);
  await page.goto("/");
  await expect(page.getByText("Documents")).toBeVisible();

  await page.getByRole("button", { name: "New item inside Documents" }).click();
  await expect(page.getByLabel("Name for the new file")).toBeVisible();
  await expect(page.locator(".new-where.fixed")).toContainText("Documents");
});
