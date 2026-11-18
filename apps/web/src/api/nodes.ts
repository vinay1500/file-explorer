import { request } from "./client";
import type { Deleted, Node, Page } from "./contract";

/** One page of the caller's root folders and files. */
export async function listRootsPage(cursor?: string): Promise<Page> {
  const query = cursor === undefined ? "" : `?cursor=${encodeURIComponent(cursor)}`;
  return request<Page>(`/api/v1/roots${query}`);
}

/** One page of a folder's contents. A *file* answers 409, not an empty page. */
export async function listChildrenPage(id: number, cursor?: string): Promise<Page> {
  const query = cursor === undefined ? "" : `?cursor=${encodeURIComponent(cursor)}`;
  return request<Page>(`/api/v1/nodes/${id}/children${query}`);
}

/**
 * Pages are followed to the end, and that is a correctness requirement rather
 * than a convenience.
 *
 * The server pages at 50 (`limits.py:34`) against a 50,000-node ceiling, and
 * nothing in this client used to read `next_cursor`. With fifty roots sorting
 * before it, a node dragged to the root would be moved by the server, missed
 * by a single-page refetch, and left drawn inside the folder it had just left
 * -- **a tree the server does not have**, which is the exact failure D-23
 * dropped optimistic updates to avoid. Refetching instead of patching only
 * helps if the refetch sees everything.
 *
 * `MAX_PAGES` bounds it. A cursor that never advances would otherwise spin
 * forever, and an infinite loop in a data layer is a worse failure than a
 * truncated list: it stops being a tree that is wrong and becomes a tab that
 * is dead.
 */
const MAX_PAGES = 64;

async function collect(fetchPage: (cursor?: string) => Promise<Page>): Promise<Node[]> {
  const items: Node[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const result: Page = await fetchPage(cursor);
    items.push(...(result.items ?? []));
    const next = result.next_cursor;
    if (next === null || next === undefined || next === cursor) return items;
    cursor = next;
  }
  return items;
}

/** Every root the caller owns, following pages. */
export async function listRoots(): Promise<Node[]> {
  return collect((cursor) => listRootsPage(cursor));
}

/** Every child of a folder, following pages. */
export async function listChildren(id: number): Promise<Node[]> {
  return collect((cursor) => listChildrenPage(id, cursor));
}

export async function createNode(
  name: string,
  type: "file" | "folder",
  parentId: number | null,
): Promise<Node> {
  return request<Node>("/api/v1/nodes", {
    method: "POST",
    body: { name, type, parent_id: parentId },
  });
}

export async function renameNode(id: number, name: string): Promise<Node> {
  return request<Node>(`/api/v1/nodes/${id}`, { method: "PATCH", body: { name } });
}

export async function deleteNode(id: number): Promise<Deleted> {
  return request<Deleted>(`/api/v1/nodes/${id}`, { method: "DELETE" });
}

/**
 * `null` moves the node to the root. That is the whole of F-06: the original
 * client could not express it, so the feature did not exist, so nobody could
 * get a folder back out of another folder.
 *
 * The type of this parameter is checked at build time -- `contract.ts` asserts
 * that `null` stays assignable to the generated `new_parent_id`. If the API
 * ever narrows it, this file stops compiling rather than quietly losing the
 * feature again.
 */
export async function moveNode(id: number, newParentId: number | null): Promise<Node> {
  return request<Node>(`/api/v1/nodes/${id}/move`, {
    method: "POST",
    body: { new_parent_id: newParentId },
  });
}

