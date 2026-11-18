import type { NodeModel } from "@minoru/react-dnd-treeview";
import type { Node } from "../api/contract";

/**
 * The drag library's root.
 *
 * It identifies the root by a `parent` that matches `rootId`, which defaults
 * to `0`. The API says `parent_id: null`. **That mismatch is where F-06
 * lives** -- a client that does not translate it cannot express "move this to
 * the root", because `0` is a perfectly good node id and `null` is not a value
 * the library understands.
 *
 * So the translation happens in exactly two functions, here, and nowhere else.
 */
export const ROOT_ID = 0;

export interface NodeData {
  type: "file" | "folder";
}

/** API node -> library node. `parent_id: null` becomes `ROOT_ID`. */
export function toTreeNode(node: Node): NodeModel<NodeData> {
  const type = node.type === "folder" ? "folder" : "file";
  // `Node.id` is optional in the generated types because the document marks
  // no field required, so TypeScript demands a fallback here. It used to be
  // `?? 0` -- and `0` is `ROOT_ID`, the sentinel this entire file exists to
  // manage, so a node that arrived without an id would have *become the
  // root*. The line below it, `parent_id ?? ROOT_ID`, is correct and reads
  // identically, which is what made it easy to miss.
  //
  // A node without an id is a malformed response, not a state to render.
  if (node.id === undefined) {
    throw new Error(`the API returned a node with no id: ${JSON.stringify(node)}`);
  }
  return {
    id: node.id,
    parent: node.parent_id ?? ROOT_ID,
    text: node.name ?? "",
    // `droppable` is what the library's own rules use to refuse a file as a
    // destination. Setting it correctly is why this application needs no
    // custom `canDrop` at all -- see `Explorer.tsx`.
    droppable: type === "folder",
    data: { type },
  };
}

/** Library parent -> API `parent_id`. `ROOT_ID` becomes `null`. */
export function toParentId(parent: NodeModel["id"]): number | null {
  if (parent === ROOT_ID || parent === "0") return null;
  return typeof parent === "number" ? parent : Number(parent);
}

/** Merge a freshly loaded page into the flat list, replacing what it covers. */
export function mergeNodes(
  existing: NodeModel<NodeData>[],
  incoming: NodeModel<NodeData>[],
): NodeModel<NodeData>[] {
  const byId = new Map(existing.map((n) => [n.id, n]));
  for (const node of incoming) byId.set(node.id, node);
  return [...byId.values()];
}

/** Every id at or beneath `id`, among the nodes currently loaded. */
export function subtreeIds(nodes: NodeModel<NodeData>[], id: NodeModel["id"]): Set<NodeModel["id"]> {
  const found = new Set<NodeModel["id"]>([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const node of nodes) {
      if (!found.has(node.id) && found.has(node.parent)) {
        found.add(node.id);
        grew = true;
      }
    }
  }
  return found;
}
