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
  // The form still says where it is going -- it is rendered above the tree
  // rather than as a row inside the folder, so without that there is nothing
  // on screen naming the destination. It states it; it no longer asks.
  await expect(page.locator(".new-where.fixed")).toContainText("projects");
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

/**
 * F-73, and it is F-62 wearing different clothes.
 *
 * Every test above passed while the defect was live, and the reason is the
 * same one that let a browser test stay green through F-62: **Playwright
 * clicks a control by name, and `opacity: 0` does not stop it.** The button
 * that creates something inside a folder was there, took the right parent and
 * sent the right body -- and a person could not see it, because the row
 * actions were transparent until the pointer was over that exact row. So the
 * only control anybody could find was the header button, which creates at the
 * top level, and Vinay reported the obvious consequence: *"new folder and
 * files are always made on root not inside a folder."*
 *
 * The two tests below are the ones that would have caught it. One asks what a
 * person can see rather than what the DOM contains; the other gives the
 * header form a way to say where the thing goes, and proves the body changes.
 */
test("the controls on a row are visible without hovering over it", async ({ page }) => {
  const created: Created = { bodies: [] };
  await stubApi(page, created);
  await page.goto("/");
  await expect(page.getByText("projects")).toBeVisible();

  // Deliberately no hover, no focus, no click anywhere near the row. This is
  // the state a person is in when they open the page and look at it.
  const opacity = await page
    .getByRole("button", { name: "New item inside projects" })
    .evaluate((el) => {
      const actions = el.closest(".actions");
      return actions === null ? 0 : Number(getComputedStyle(actions).opacity);
    });

  // 0.55 today. The assertion is "a person can see that this is there", not
  // the exact value -- but zero is the defect and zero must fail.
  expect(opacity).toBeGreaterThan(0.2);
});

test("the header form can send the new item into a folder", async ({ page }) => {
  const created: Created = { bodies: [] };
  await stubApi(page, created);
  await page.goto("/");
  await expect(page.getByText("projects")).toBeVisible();

  // Starting from the header button -- the route every person actually took.
  await page.getByRole("button", { name: "New folder" }).click();
  await expect(page.getByLabel("Where the new folder goes")).toHaveValue("");
  await page.getByLabel("Where the new folder goes").selectOption({ label: "projects" });
  await page.getByLabel("Name for the new folder").fill("2027");
  await page.getByRole("button", { name: "Create" }).click();

  await expect.poll(() => created.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0);
  // The body is the assertion. A redraw assertion would pass against a client
  // that sends parent_id null and then re-reads a tree that disagrees.
  expect(created.bodies[0]).toEqual({ name: "2027", type: "folder", parent_id: 1 });
});

test("a nested folder is offered by its path, not by its bare name", async ({ page }) => {
  const created: Created = { bodies: [] };
  await stubApi(page, created);
  await page.goto("/");

  // Open `projects` so its child is loaded and can be offered as a target.
  await page.getByRole("button", { name: "Expand projects" }).click();
  await expect(page.getByText("2026")).toBeVisible();

  await page.getByRole("button", { name: "New file" }).click();
  // Two folders named "2026" under different parents are the ordinary case in
  // a tree, and a picker that lists bare names cannot tell them apart.
  await page.getByLabel("Where the new file goes").selectOption({ label: "projects / 2026" });
  await page.getByLabel("Name for the new file").fill("plan.md");
  await page.getByRole("button", { name: "Create" }).click();

  await expect.poll(() => created.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0);
  expect(created.bodies[0]).toEqual({ name: "plan.md", type: "file", parent_id: 2 });
});

/**
 * F-76. Vinay, using it: *"if we clicked + on some folder then we already want
 * to make a file in that folder not anywhere else, but here we can select a
 * different folder as well that is i think not correct."*
 *
 * He is right, and the code already agreed with him in a comment: the row's
 * button answers "put something in **this** folder" and the header button
 * asks "where does this go". Two questions, and one picker was answering both
 * -- so the gesture that settled the destination was immediately followed by
 * a control inviting a different one.
 *
 * These two tests are a pair on purpose. Removing the picker everywhere would
 * reopen F-73, where creating inside a folder was possible and unreachable.
 * The second test is what stops the fix from going too far.
 */
test("the plus on a row settles the destination, and offers no way to change it", async ({
  page,
}) => {
  const created: Created = { bodies: [] };
  await stubApi(page, created);
  await page.goto("/");
  await expect(page.getByText("projects")).toBeVisible();

  await page.getByRole("button", { name: "New item inside projects" }).click();

  // The question is not asked again.
  await expect(page.getByLabel("Where the new file goes")).toHaveCount(0);
  // And the answer is on screen, which is the half of the picker worth having.
  await expect(page.locator(".new-where.fixed")).toContainText("projects");
});

test("the header button still asks, because nothing has answered yet", async ({ page }) => {
  const created: Created = { bodies: [] };
  await stubApi(page, created);
  await page.goto("/");
  await expect(page.getByText("projects")).toBeVisible();

  await page.getByRole("button", { name: "New file", exact: true }).click();
  await expect(page.getByLabel("Where the new file goes")).toBeVisible();

  // And choosing a folder there must not turn the picker into a statement
  // half way through -- the reason `fixed` is its own field and not
  // `parent !== null`.
  await page.getByLabel("Where the new file goes").selectOption("1");
  await expect(page.getByLabel("Where the new file goes")).toBeVisible();
  await expect(page.getByLabel("Where the new file goes")).toHaveValue("1");
});

test("the settled destination is readable, not just present in the DOM", async ({ page }) => {
  // F-62 and F-73 were both controls that existed, passed their tests, and
  // could not be seen. A destination nobody can read is the same bug wearing
  // the fix's clothes.
  const created: Created = { bodies: [] };
  await stubApi(page, created);
  await page.goto("/");
  await expect(page.getByText("projects")).toBeVisible();

  await page.getByRole("button", { name: "New item inside projects" }).click();
  const where = page.locator(".new-where.fixed");
  await expect(where).toBeVisible();
  const box = await where.boundingBox();
  expect(box).not.toBeNull();
  expect(box?.height ?? 0).toBeGreaterThan(8);
  expect(
    await where.evaluate((el) => Number(getComputedStyle(el).opacity)),
  ).toBeGreaterThan(0.9);
});

test("a nested destination is stated by its path, not its bare name", async ({ page }) => {
  const created: Created = { bodies: [] };
  await stubApi(page, created);
  await page.goto("/");
  await page.getByRole("button", { name: "Expand projects" }).click();
  await expect(page.getByText("2026")).toBeVisible();

  await page.getByRole("button", { name: "New item inside 2026" }).click();
  // "2026" alone is ambiguous the moment a second folder is called 2026.
  await expect(page.locator(".new-where.fixed")).toContainText("projects / 2026");

  await page.getByLabel("Name for the new file").fill("notes.md");
  await page.getByRole("button", { name: "Create" }).click();
  await expect.poll(() => created.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0);
  expect(created.bodies[0]).toEqual({ name: "notes.md", type: "file", parent_id: 2 });
});
