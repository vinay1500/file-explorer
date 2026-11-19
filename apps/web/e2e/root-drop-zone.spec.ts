import { expect, test, type JSHandle, type Locator, type Page, type Route } from "@playwright/test";

/**
 * F-62: the root drop target existed and had no pixels.
 *
 * `drag-to-root.spec.ts` proves the client can *say* "move this to the root".
 * It does that by dispatching `dragover` and `drop` at `.tree-root` by name,
 * and `dispatchEvent` skips hit testing entirely -- so it passed while the
 * container's height was exactly the sum of its rows and every pixel of it was
 * covered by a row. A pointer could never produce `dropTargetNode ===
 * undefined`, which is the library's way of saying "the root". The application
 * was unusable for the one gesture the project is named after and the suite
 * was green, because the test aimed at an element and a person aims at a
 * point.
 *
 * So the first test here never names the drop target: it asks the document
 * what is under each coordinate, the way a browser does during a drag, and
 * fails if nothing below the last row belongs to the container. The drag that
 * follows is aimed at a point that probe returned. The dispatch still has to
 * name an element -- that is the only way to drive HTML5 drag events from
 * Playwright at all -- and what makes this file honest is the probe in front
 * of it, not the dispatch.
 */

const WHOAMI = { id: 1, email: "guest@guests.invalid", csrf_token: "test-token" };
const ROOTS = {
  items: [
    { id: 1, parent_id: null, name: "projects", type: "folder" },
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

interface Captured {
  bodies: unknown[];
}

async function stubApi(page: Page, captured: Captured): Promise<void> {
  await page.route("**/api/v1/health", (r) => json(r, { status: "ok", db: "ok" }));
  await page.route("**/api/v1/auth/me", (r) => json(r, WHOAMI));
  await page.route("**/api/v1/roots*", (r) => json(r, ROOTS));
  await page.route("**/api/v1/nodes/1/children*", (r) => json(r, CHILDREN_OF_1));
  await page.route("**/api/v1/nodes/2/children*", (r) => json(r, { items: [], next_cursor: null }));
  await page.route("**/api/v1/nodes/3/children*", (r) => json(r, { items: [], next_cursor: null }));
  await page.route("**/api/v1/nodes/*/move", async (route) => {
    captured.bodies.push(route.request().postDataJSON());
    await json(route, { id: 2, parent_id: null, name: "2026", type: "folder" });
  });
}

/** Where the drop band is, in page coordinates, and what is actually there. */
async function probeRootBand(page: Page): Promise<{
  samples: number;
  hits: number;
  point: { x: number; y: number } | null;
  other: string[];
}> {
  return page.evaluate(() => {
    const root = document.querySelector(".tree-root");
    if (!root) return { samples: 0, hits: 0, point: null, other: ["no .tree-root"] };
    const box = root.getBoundingClientRect();
    const rows = [...root.querySelectorAll("li")].map((li) => li.getBoundingClientRect());
    const lowestRow = rows.reduce((low, r) => Math.max(low, r.bottom), box.top);

    let hits = 0;
    let samples = 0;
    let point: { x: number; y: number } | null = null;
    const other = new Set<string>();
    // Only the area below the last row: the rows themselves are supposed to
    // be their own drop targets, and a hit there would be the wrong answer.
    for (let y = Math.ceil(lowestRow) + 2; y < box.bottom - 2; y += 4) {
      for (let x = Math.ceil(box.left) + 4; x < box.right - 4; x += 24) {
        samples += 1;
        const found = document.elementFromPoint(x, y);
        if (found === root) {
          hits += 1;
          point ??= { x, y };
        } else {
          other.add(found ? `${found.tagName}.${String(found.className)}` : "null");
        }
      }
    }
    return { samples, hits, point, other: [...other] };
  });
}

async function dragToPoint(
  page: Page,
  source: Locator,
  at: { x: number; y: number },
): Promise<void> {
  const dataTransfer: JSHandle<DataTransfer> = await page.evaluateHandle(() => new DataTransfer());
  await source.dispatchEvent("dragstart", { dataTransfer });

  // `at` came from `probeRootBand`, which asked the document what is under
  // each coordinate, so the aiming is what a pointer would do and the probe
  // fails first if nothing there belongs to the container.
  //
  // The dispatch itself names the element, because `dispatchEvent` is the
  // only way to drive HTML5 drag events from Playwright. An earlier version
  // of this comment claimed the target was never a selector, directly above a
  // line that selects one.
  const target = page.locator(".tree-root");
  await target.dispatchEvent("dragenter", { dataTransfer });
  await target.dispatchEvent("dragover", { dataTransfer, clientX: at.x, clientY: at.y });
  await expect(target).toHaveClass(/tree-drop/, { timeout: 5_000 });
  await target.dispatchEvent("dragover", { dataTransfer, clientX: at.x, clientY: at.y });
  await page.waitForTimeout(100);
  await target.dispatchEvent("drop", { dataTransfer, clientX: at.x, clientY: at.y });
  await source.dispatchEvent("dragend", { dataTransfer });
  await dataTransfer.dispose();
}

test("the tree's root container has an area a pointer can actually land on", async ({ page }) => {
  const captured: Captured = { bodies: [] };
  await stubApi(page, captured);
  await page.goto("/");
  await expect(page.getByText("projects")).toBeVisible();

  const probe = await probeRootBand(page);

  // The measurement that was zero on the deployed site, with a margin so the
  // band cannot shrink to a sliver and still pass.
  expect(
    probe.hits,
    `no point below the last row belongs to .tree-root; ${String(probe.samples)} sampled, ` +
      `found instead: ${probe.other.join(", ")}`,
  ).toBeGreaterThan(20);
});

test("the band that tells a person where to aim is actually drawn", async ({ page }) => {
  const captured: Captured = { bodies: [] };
  await stubApi(page, captured);
  await page.goto("/");
  await expect(page.getByText("projects")).toBeVisible();

  // The hit area comes from the container's padding box; the band is paint,
  // and deleting the whole `::after` rule leaves every other test in this
  // file green. That would make the affordance -- the only thing on screen
  // telling a visitor the gesture exists -- untested, which is how F-62
  // survived: the capability was there and nothing said so.
  const band = await page.evaluate(() => {
    const root = document.querySelector(".tree-root");
    if (!root) return null;
    const after = window.getComputedStyle(root, "::after");
    return { content: after.content, height: parseFloat(after.height) };
  });

  expect(band).not.toBeNull();
  expect(band!.content).toContain("top level");
  expect(band!.height).toBeGreaterThan(20);
});

test("a drop aimed at that area moves the folder to the root", async ({ page }) => {
  const captured: Captured = { bodies: [] };
  await stubApi(page, captured);
  await page.goto("/");
  await page.getByRole("button", { name: "Expand projects" }).click();
  await expect(page.getByText("2026")).toBeVisible();

  const probe = await probeRootBand(page);
  expect(probe.point, "the drop band is not reachable by a pointer").not.toBeNull();

  const source = page.locator("li").filter({ hasText: "2026" }).last();
  await dragToPoint(page, source, probe.point!);

  await expect.poll(() => captured.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0);
  expect(captured.bodies[0]).toEqual({ new_parent_id: null });
});

test("deleting a folder nobody has opened does not promise it is empty", async ({ page }) => {
  const captured: Captured = { bodies: [] };
  await stubApi(page, captured);
  await page.goto("/");
  await expect(page.getByText("projects")).toBeVisible();

  // `projects` has a child and the client has never fetched it, so the count
  // this dialog can compute is zero. A dialog whose whole job is "do you know
  // how many there are" must not answer that with silence.
  await page.getByRole("button", { name: "Delete projects" }).click();

  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("everything inside it");
  await expect(dialog).toContainText("cannot tell you how many");
});

test("the move-to-top button does it without a drag at all", async ({ page }) => {
  const captured: Captured = { bodies: [] };
  await stubApi(page, captured);
  await page.goto("/");
  await page.getByRole("button", { name: "Expand projects" }).click();
  await expect(page.getByText("2026")).toBeVisible();

  // Keyboard-reachable, which a drag is not. The button exists only on rows
  // that are not already at the top level.
  await page.getByRole("button", { name: "Move 2026 to the top level" }).click();

  await expect.poll(() => captured.bodies.length, { timeout: 5_000 }).toBeGreaterThan(0);
  expect(captured.bodies[0]).toEqual({ new_parent_id: null });

  // And it is absent where it would be a no-op.
  await expect(page.getByRole("button", { name: "Move archive to the top level" })).toHaveCount(0);
});
