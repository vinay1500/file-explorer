import { describe, expect, it } from "vitest";
import type { NodeModel } from "@minoru/react-dnd-treeview";
import type { Node } from "../api/contract";
import { ROOT_ID, mergeNodes, subtreeIds, toParentId, toTreeNode, type NodeData } from "./model";

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
