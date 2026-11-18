import { request } from "./client";
import type { Deleted, Node, Page, Path } from "./contract";

/** One page of the caller's root folders and files. */
export async function listRoots(cursor?: string): Promise<Page> {
  const query = cursor === undefined ? "" : `?cursor=${encodeURIComponent(cursor)}`;
  return request<Page>(`/api/v1/roots${query}`);
}

/** One page of a folder's contents. A *file* answers 409, not an empty page. */
export async function listChildren(id: number, cursor?: string): Promise<Page> {
  const query = cursor === undefined ? "" : `?cursor=${encodeURIComponent(cursor)}`;
  return request<Page>(`/api/v1/nodes/${id}/children${query}`);
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

export async function breadcrumbs(id: number): Promise<Path> {
  return request<Path>(`/api/v1/nodes/${id}/path`);
}
