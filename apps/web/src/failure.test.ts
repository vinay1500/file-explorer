import { describe, expect, it } from "vitest";
import { explainFailure } from "./failure";
import { ApiError } from "./api/client";
import { StillWaking } from "./api/wake";

/**
 * F-77, and the class it belongs to rather than the instance.
 *
 * Vinay opened the demo and got `request failed with 429 (HTTP 429)`. The
 * comment at the top of `explainFailure` had forbidden exactly that since M8
 * -- *"a status code is not a sentence"* -- and the function then handled
 * three statuses by name and returned `error.message` for the rest.
 * `ApiError`'s message is `request failed with <status>` whenever the body is
 * not a problem document, so every status nobody had thought of printed the
 * forbidden string. 429 was simply the one that came up.
 *
 * A test for 429 would close 429. These drive the function over a **list**,
 * because the defect was the fall-through, not the status.
 */

// Every status this API is documented to return, plus the ones an edge or a
// host can produce on its own. 429 is in here twice on purpose: ours carries
// a problem document and the host's does not, and they need different
// sentences.
const STATUSES = [400, 401, 403, 404, 405, 406, 409, 413, 415, 422, 429, 500, 502, 503, 504, 520];

const FORBIDDEN = /request failed with/i;

describe("explainFailure", () => {
  it("never prints a bare status code, for any status, with no problem document", () => {
    for (const status of STATUSES) {
      const state = explainFailure(new ApiError(status, null));
      expect(state.kind).toBe("failed");
      if (state.kind !== "failed") continue;
      expect(state.message, `status ${String(status)}`).not.toMatch(FORBIDDEN);
      // And it is a sentence, not a fragment or a code.
      expect(state.message.length, `status ${String(status)}`).toBeGreaterThan(30);
      expect(state.message.trim().endsWith("."), `status ${String(status)}`).toBe(true);
    }
  });

  it("never prints a bare status code when a problem document is present either", () => {
    for (const status of STATUSES) {
      const state = explainFailure(
        new ApiError(status, { title: "Too Many Requests", code: "RATE_LIMITED" }),
      );
      if (state.kind !== "failed") continue;
      expect(state.message, `status ${String(status)}`).not.toMatch(FORBIDDEN);
    }
  });

  it("tells the host's wake throttle apart from our own rate limit", () => {
    // Ours: a problem document, code RATE_LIMITED. Measured shape.
    const ours = explainFailure(
      new ApiError(429, { title: "Too Many Requests", code: "RATE_LIMITED" }),
    );
    // The host's: 429 with a text/plain body, so nothing parses and
    // `problem` is null. Measured 21 Nov through the rewrite, carrying
    // `x-render-routing: hibernate-rate-limited`.
    const theirs = explainFailure(new ApiError(429, null));

    if (ours.kind !== "failed" || theirs.kind !== "failed") throw new Error("both are failures");
    expect(ours.message).not.toBe(theirs.message);
    expect(ours.message).toMatch(/per-minute|seconds/i);
    expect(theirs.message).toMatch(/woken|wake/i);
    // Neither offers a button that would make it worse. The host throttle is
    // earned by knocking on the origin, which is what the wake button does.
    expect(ours.canWake).toBe(false);
    expect(theirs.canWake).toBe(false);
  });

  it("still keeps the three it already got right", () => {
    const waking = explainFailure(new StillWaking(51_000, null));
    const unreachable = explainFailure(new ApiError(503, null));
    const cold = explainFailure(new ApiError(502, null));
    if (waking.kind !== "failed" || unreachable.kind !== "failed" || cold.kind !== "failed") {
      throw new Error("all three are failures");
    }
    expect(waking.canWake).toBe(true);
    expect(waking.message).toMatch(/51 seconds/);
    // 503 is the database being unreachable, which waiting does not fix.
    expect(unreachable.canWake).toBe(false);
    expect(cold.canWake).toBe(true);
  });

  it("says something human when nothing answered at all", () => {
    const state = explainFailure(new TypeError("Failed to fetch"));
    if (state.kind !== "failed") throw new Error("failure");
    expect(state.message).not.toMatch(FORBIDDEN);
    expect(state.message).not.toMatch(/Failed to fetch/);
  });
});
