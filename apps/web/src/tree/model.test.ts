import { describe, expect, it } from "vitest";
import type { NodeModel } from "@minoru/react-dnd-treeview";
import type { Node } from "../api/contract";
import {
  ROOT_ID,
  crumbsFor,
  mergeNodes,
  subtreeIds,
  toParentId,
  toTreeNode,
  type NodeData,
} from "./model";

/**
 * These four functions are where F-06 lived.
 *
 * The drag library calls the root `0`; the API calls it `null`. A client that
 * does not translate cannot express "move this to the root" -- `0` is a real
 * node id and `null` is not a value the library understands. The bug was never
 * in the move algorithm, which has handled a null destination since M2.
 */

const node = (over: Partial<Node>): Node => ({
  id: 1,
  parent_id: null,
  name: "home",
  type: "folder",
  ...over,
});

describe("toTreeNode", () => {
  it("maps a root's null parent to the library's root id", () => {
    expect(toTreeNode(node({ parent_id: null })).parent).toBe(ROOT_ID);
  });

  it("keeps a real parent id as itself", () => {
    expect(toTreeNode(node({ id: 7, parent_id: 3 })).parent).toBe(3);
  });

  it("marks folders droppable and files not", () => {
    // This is what makes a custom `canDrop` unnecessary: the library's own
    // rule refuses a non-droppable destination. Getting this wrong is how a
    // client ends up writing a callback that replaces four rules to restore
    // one.
    expect(toTreeNode(node({ type: "folder" })).droppable).toBe(true);
    expect(toTreeNode(node({ type: "file" })).droppable).toBe(false);
  });
});

describe("toParentId", () => {
  it("maps the library's root id back to null", () => {
    // The direction that matters. If this returned 0, the API would be asked
    // to re-parent the node under node 0 -- which is a 404, not a move to the
    // root, and the feature silently does not exist.
    expect(toParentId(ROOT_ID)).toBeNull();
  });

  it("maps a string root id back to null too", () => {
    expect(toParentId("0")).toBeNull();
  });

  it("round-trips a real parent", () => {
    expect(toParentId(toTreeNode(node({ id: 7, parent_id: 3 })).parent)).toBe(3);
  });

  it("round-trips the root", () => {
    expect(toParentId(toTreeNode(node({ parent_id: null })).parent)).toBeNull();
  });
});

describe("mergeNodes", () => {
  const tn = (id: number, parent: number): NodeModel<NodeData> => ({
    id,
    parent,
    text: `n${String(id)}`,
    droppable: true,
    data: { type: "folder" },
  });

  it("replaces a node rather than duplicating it", () => {
    const merged = mergeNodes([tn(1, 0)], [{ ...tn(1, 0), text: "renamed" }]);
    expect(merged).toHaveLength(1);
    expect(merged[0]?.text).toBe("renamed");
  });

  it("keeps nodes the incoming page does not mention", () => {
    expect(mergeNodes([tn(1, 0), tn(2, 0)], [tn(2, 0)])).toHaveLength(2);
  });
});

describe("subtreeIds", () => {
  const tn = (id: number, parent: number): NodeModel<NodeData> => ({
    id,
    parent,
    text: `n${String(id)}`,
    droppable: true,
    data: { type: "folder" },
  });

  it("finds a node's whole loaded subtree, however deep", () => {
    // Depth matters: a single-level walk would report one child and the
    // delete confirmation would understate what it is about to destroy.
    const nodes = [tn(1, 0), tn(2, 1), tn(3, 2), tn(4, 3), tn(9, 0)];
    expect(subtreeIds(nodes, 1)).toEqual(new Set([1, 2, 3, 4]));
  });

  it("does not wander into a sibling", () => {
    const nodes = [tn(1, 0), tn(2, 1), tn(9, 0), tn(10, 9)];
    expect(subtreeIds(nodes, 9)).toEqual(new Set([9, 10]));
  });

  it("returns just the node when nothing is loaded beneath it", () => {
    expect(subtreeIds([tn(1, 0)], 1)).toEqual(new Set([1]));
  });
});

describe("crumbsFor", () => {
  const tn = (id: number, parent: number, text: string): NodeModel<NodeData> => ({
    id,
    parent,
    text,
    droppable: true,
    data: { type: "folder" },
  });

  it("reads from the top level down, not from the node up", () => {
    const nodes = [tn(1, 0, "Documents"), tn(2, 1, "Invoices"), tn(3, 2, "march.pdf")];
    const { crumbs, complete } = crumbsFor(nodes, 3);
    expect(crumbs.map((c) => c.text)).toEqual(["Documents", "Invoices", "march.pdf"]);
    expect(complete).toBe(true);
  });

  it("is a single crumb for something at the top level", () => {
    const { crumbs, complete } = crumbsFor([tn(1, 0, "Documents")], 1);
    expect(crumbs.map((c) => c.text)).toEqual(["Documents"]);
    expect(complete).toBe(true);
  });

  it("does not pick up a sibling branch on the way up", () => {
    const nodes = [tn(1, 0, "Documents"), tn(9, 0, "Photos"), tn(2, 1, "Invoices")];
    expect(crumbsFor(nodes, 2).crumbs.map((c) => c.text)).toEqual(["Documents", "Invoices"]);
  });

  it("reports a path that does not reach the top as incomplete", () => {
    // The parent is missing, so the chain starts in the middle. Returning
    // ["Invoices", "march.pdf"] with no flag would read as a top-level
    // Invoices folder, which is a different and wrong statement.
    const nodes = [tn(2, 1, "Invoices"), tn(3, 2, "march.pdf")];
    const { crumbs, complete } = crumbsFor(nodes, 3);
    expect(crumbs.map((c) => c.text)).toEqual(["Invoices", "march.pdf"]);
    expect(complete).toBe(false);
  });

  it("reports an unknown id as incomplete rather than empty-and-fine", () => {
    expect(crumbsFor([tn(1, 0, "Documents")], 404)).toEqual({ crumbs: [], complete: false });
  });

  it("terminates on a parent cycle instead of hanging the page", () => {
    // The schema cannot produce this and the move algorithm refuses it. The
    // bound exists so that a bug somewhere else cannot lock the browser.
    const nodes = [tn(1, 2, "a"), tn(2, 1, "b")];
    const { complete } = crumbsFor(nodes, 1);
    expect(complete).toBe(false);
  });
});
