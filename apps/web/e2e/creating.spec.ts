import { expect, test, type Page, type Route } from "@playwright/test";

/**
 * Making a file, and making it in the right place.
 *
 * `POST /api/v1/nodes` has taken `type` and `parent_id` since M3a. The
 * interface offered neither: one button, marked **New folder**, that always
 * sent `{"type": "folder", "parent_id": null}`. So the seeded tree contained
 * files a visitor had no way to produce, and anything they did make landed at
 * the top level whatever they had selected.
 *
 * Vinay found it by using the thing: *"there is no way to make a new file only
 * a new folder."* That is the fourth defect found that way and none of them
 * were found by a test, which is the argument for the test below asserting the
 * **request body** rather than the redraw: a redraw assertion passes against a
 * client that sends the wrong type and re-reads a tree that disagrees.
 */

const WHOAMI = { id: 1, email: "guest@guests.invalid", csrf_token: "test-token" };
const ROOTS = {
  items: [{ id: 1, parent_id: null, name: "projects", type: "folder" }],
  next_cursor: null,
};
const CHILDREN_OF_1 = {
  items: [{ id: 2, parent_id: 1, name: "2026", type: "folder" }],
  next_cursor: null,
};

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

interface Created {
  bodies: { name?: string; type?: string; parent_id?: number | null }[];
}

async function stubApi(page: Page, created: Created): Promise<void> {
  await page.route("**/api/v1/health", (r) => json(r, { status: "ok", db: "ok" }));
  await page.route("**/api/v1/auth/me", (r) => json(r, WHOAMI));
  await page.route("**/api/v1/roots*", (r) => json(r, ROOTS));
  await page.route("**/api/v1/nodes/1/children*", (r) => json(r, CHILDREN_OF_1));
  await page.route("**/api/v1/nodes/2/children*", (r) => json(r, { items: [], next_cursor: null }));
  await page.route("**/api/v1/nodes", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    created.bodies.push(route.request().postDataJSON() as Created["bodies"][number]);
    await json(route, { id: 99, parent_id: null, name: "made", type: "file" }, 201);
  });
}

test("a file can be made at the top level, and it is sent as a file", async ({ page }) => {
  const created: Created = { bodies: [] };
  await stubApi(page, created);
  await page.goto("/");
  await expect(page.getByText("projects")).toBeVisible();

  await page.getByRole("button", { name: "New file" }).click();
  await page.getByLabel("Name for the new file").fill("notes.txt");
  await page.getByRole("button", { name: "Create" }).click();

  await expect.poll(() => created.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0);
  // `type: "file"` is the whole point. The old button sent "folder" whatever
  // the person meant.
  expect(created.bodies[0]).toEqual({ name: "notes.txt", type: "file", parent_id: null });
});

test("a folder is still a folder", async ({ page }) => {
  // The complement, so the first test is not vacuous: if everything were sent
  // as a file now, test one would pass while the application was broken in the
  // opposite direction.
  const created: Created = { bodies: [] };
  await stubApi(page, created);
  await page.goto("/");

  await page.getByRole("button", { name: "New folder" }).click();
  await page.getByLabel("Name for the new folder").fill("drafts");
  await page.getByRole("button", { name: "Create" }).click();

  await expect.poll(() => created.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0);
  expect(created.bodies[0]).toEqual({ name: "drafts", type: "folder", parent_id: null });
});

test("the button on a folder puts the new item inside that folder", async ({ page }) => {
  const created: Created = { bodies: [] };
  await stubApi(page, created);
  await page.goto("/");
  await expect(page.getByText("projects")).toBeVisible();

  await page.getByRole("button", { name: "New item inside projects" }).click();
  // The form says where it is going, because it is rendered above the tree
  // rather than as a row inside the folder.
  await expect(page.getByText("in projects")).toBeVisible();
  await page.getByLabel("Name for the new file").fill("README.md");
  await page.getByRole("button", { name: "Create" }).click();

  await expect.poll(() => created.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0);
  // `parent_id: 1`, not null. A file that can only be made at the top level
  // and then dragged into place is a worse answer than no button.
  expect(created.bodies[0]).toEqual({ name: "README.md", type: "file", parent_id: 1 });
});

test("the type can be switched after the form is open", async ({ page }) => {
  const created: Created = { bodies: [] };
  await stubApi(page, created);
  await page.goto("/");
  await expect(page.getByText("projects")).toBeVisible();

  // The per-folder button opens on "file"; a person who wanted a subfolder
  // should not have to close it and start again.
  await page.getByRole("button", { name: "New item inside projects" }).click();
  await page.getByRole("button", { name: "Folder", exact: true }).click();
  await page.getByLabel("Name for the new folder").fill("sub");
  await page.getByRole("button", { name: "Create" }).click();

  await expect.poll(() => created.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0);
  expect(created.bodies[0]).toEqual({ name: "sub", type: "folder", parent_id: 1 });
});
