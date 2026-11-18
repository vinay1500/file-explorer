import { request } from "./client";

/**
 * `/api/v1/health` is deliberately outside the OpenAPI document: it exists for
 * the platform's probe, not for clients, and publishing it would invite one to
 * depend on it. So its success shape is declared here by hand -- the only
 * hand-written response type in this directory, and it says why.
 */
export interface Health {
  status: "ok" | "degraded";
  db: "ok" | "unreachable";
}

export async function probeHealth(): Promise<Health> {
  return request<Health>("/api/v1/health");
}
