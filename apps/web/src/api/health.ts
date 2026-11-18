export interface Health {
  status: "ok" | "degraded";
  db: "ok" | "unreachable";
}

/**
 * Relative URL, deliberately. See the comment in `App.tsx`.
 *
 * `credentials: "same-origin"` is the default for `fetch`, but it is written
 * out because the session cookie depends on it and a default that matters is
 * worth saying. A cookie is only sent when the request is same-origin, which
 * is exactly what the rewrite arranges.
 */
export async function probeHealth(): Promise<Health> {
  const response = await fetch("/api/v1/health", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`health check returned ${response.status}`);
  }
  return (await response.json()) as Health;
}
