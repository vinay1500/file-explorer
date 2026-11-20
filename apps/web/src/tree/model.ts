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
  return forDisplay([...byId.values()]);
}

/**
 * Folders first, then files, each alphabetically.
 *
 * This is a *display* order and nothing more: the server's own ordering is by
 * name, it is what the pagination cursor encodes, and none of that changes
 * here. The client holds every page before it draws anything, so sorting the
 * merged list cannot make a row appear twice or go missing.
 *
 * It is here because of what the mixed order looked like. A file sitting
 * between two folders, with an expanded folder above it, reads as being
 * *inside* that folder - and every file manager anybody has used puts folders
 * first, so the mixed version looked broken rather than merely different.
 *
 * `localeCompare` rather than `<`, so that accented names sort where a person
 * expects rather than where their code points fall.
 */
export function forDisplay(nodes: NodeModel<NodeData>[]): NodeModel<NodeData>[] {
  return [...nodes].sort((a, b) => {
    if (a.droppable !== b.droppable) return a.droppable === true ? -1 : 1;
    return a.text.localeCompare(b.text, undefined, { numeric: true, sensitivity: "base" });
  });
}

/** Every id at or beneath `id`, among the nodes currently loaded. */
/**
 * Is everything under `id` actually known to the client?
 *
 * The tree loads children when a folder is opened, so a count taken from
 * `subtreeIds` is a count of *what has been loaded* and nothing more. The
 * delete confirmation used that number directly, which meant deleting a
 * collapsed folder asked "Delete projects?" with no mention of the forty
 * things inside it -- in a dialog whose entire purpose is to answer "do you
 * know how many there are". Found by Gate 3.
 *
 * A subtree is fully known when every folder in it has had its children
 * fetched. Files have no children to fetch, so only `droppable` nodes count.
 */
export function subtreeFullyLoaded(
  nodes: NodeModel<NodeData>[],
  loaded: Set<number>,
  id: NodeModel["id"],
): boolean {
  const ids = subtreeIds(nodes, id);
  for (const node of nodes) {
    if (!ids.has(node.id)) continue;
    if (node.droppable !== true) continue;
    const numeric = typeof node.id === "number" ? node.id : Number(node.id);
    if (!loaded.has(numeric)) return false;
  }
  // The node itself may not be in `nodes` yet in a malformed state; its own
  // children still have to have been fetched for the count to mean anything.
  const self = typeof id === "number" ? id : Number(id);
  const node = nodes.find((n) => n.id === id);
  return node?.droppable !== true || loaded.has(self);
}

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

/**
 * The chain of ancestors from the top level down to `id`, inclusive.
 *
 * The API has had `GET /nodes/{id}/path` since M3a and it answers exactly
 * this, with a recursive CTE that filters by owner in its recursive term as
 * well as its anchor. This computes it in the page instead, for one reason:
 * a node can only be looked at after its ancestors have been expanded, so
 * every ancestor is already in `nodes` and a round trip would buy nothing but
 * latency. The endpoint stays the answer for a caller that has an id and no
 * tree.
 *
 * `complete` is the part worth having. If the walk runs out of ancestors
 * before it reaches the top -- a partially loaded tree, a node whose parent
 * has been evicted -- the crumbs are still returned, and they are still true
 * as far as they go, but they no longer start at the top level. Returning
 * them unmarked would print a path that silently begins in the middle, which
 * is the failure this project keeps meeting: a correct-looking answer with no
 * way to tell it is partial.
 */
export function crumbsFor(
  nodes: NodeModel<NodeData>[],
  id: NodeModel["id"],
): { crumbs: NodeModel<NodeData>[]; complete: boolean } {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const crumbs: NodeModel<NodeData>[] = [];
  let cur = byId.get(id);
  if (cur === undefined) return { crumbs, complete: false };
  // The server caps depth at 64. The bound is against a cycle that should be
  // impossible rather than against a deep tree.
  for (let hops = 0; hops <= 64; hops += 1) {
    crumbs.unshift(cur);
    if (cur.parent === ROOT_ID) return { crumbs, complete: true };
    const next = byId.get(cur.parent);
    if (next === undefined) return { crumbs, complete: false };
    cur = next;
  }
  return { crumbs, complete: false };
}
