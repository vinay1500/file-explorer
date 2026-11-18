import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The guard for F-37, written against the source rather than the behaviour.
 *
 * Behaviour cannot catch this one cheaply. A custom `canDrop` that replaces
 * the library's rules looks *correct* in every test you would think to write
 * -- dropping a folder on a folder works, dropping on a file is refused --
 * because the rules it silently switched off are the ones about edge cases:
 * self-drop, same-parent, the root, and the ancestor walk. The April 2025
 * client passed every manual check for eighteen months with the cycle drag
 * wide open.
 *
 * So this asserts the shape of the code. From the pinned bundle (3.5.4):
 *
 *     if (canDrop) {
 *       const result = canDrop(dragSourceId, dropTargetId);
 *       if (result !== undefined) return result;
 *     }
 *     ... four rules ...
 *
 * A callback returning anything but `undefined` takes ownership of all four.
 * This application has nothing to add to them -- `droppable` already refuses a
 * file as a destination -- so it passes no callback at all.
 *
 * If one is ever genuinely needed, delete this test *deliberately* and replace
 * it with one asserting the callback returns `undefined` for every case it
 * does not actively reject. Deleting it by accident is the failure this exists
 * to prevent.
 */

const EXPLORER = readFileSync(join(__dirname, "Explorer.tsx"), "utf-8");

describe("the Tree is not given a custom canDrop", () => {
  it("passes no canDrop prop", () => {
    const code = EXPLORER.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, "");
    expect(code).not.toMatch(/\bcanDrop\s*=/);
  });

  it("sets droppable from the node type instead", () => {
    // The replacement for a custom callback, and the reason none is needed.
    const model = readFileSync(join(__dirname, "model.ts"), "utf-8");
    expect(model).toMatch(/droppable:\s*type === "folder"/);
  });

  it("still explains why, so the next reader does not add one", () => {
    // A guard whose reason is not written next to it gets removed by somebody
    // tidying up. Four of this project's findings were introduced that way.
    //
    // Whitespace and the leading `*` of a block comment are collapsed first.
    // The first version of this test matched the raw source and failed on its
    // own explanation, because the sentence wraps across two lines -- a guard
    // that fails when the prose is merely *formatted* differently is one
    // somebody deletes rather than fixes.
    const prose = EXPLORER.replace(/^\s*\*/gm, " ").replace(/\s+/g, " ");
    expect(prose).toMatch(/replaces all four rules/i);
  });
});
