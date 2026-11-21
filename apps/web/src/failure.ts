import { ApiError } from "./api/client";
import { StillWaking, isColdStart } from "./api/wake";
import type { WhoAmI } from "./api/session";

export type State =
  | { kind: "resolving" }
  | { kind: "waking"; seconds: number }
  | { kind: "anonymous" }
  | { kind: "signed-in"; who: WhoAmI }
  | { kind: "failed"; message: string; status?: number; canWake: boolean };

/**
 * Turn whatever went wrong into something a person can read.
 *
 * **`request failed with <status>` must never reach the screen.** It did
 * twice. First on the cold-start give-up path, after M8 had deleted it from
 * the first-attempt path. Then again on 21 November as
 * `request failed with 429 (HTTP 429)`, because this function handled three
 * statuses by name and returned `error.message` for everything else -- and
 * `ApiError`'s message is exactly that string whenever the body is not a
 * problem document. **The comment above forbade the thing the last branch
 * did**, which is the third time in this project prose and code have
 * disagreed inside one file (F-76 was the second).
 *
 * So the shape matters more than the cases: there is no branch here that can
 * return an unframed `error.message`, and `explains_every_status` drives this
 * function over a list rather than over the three somebody thought of.
 *
 * Exported for that test. Named `explainFailure` and not `describe` because a
 * test file importing it next to vitest's `describe` is a trap.
 */
export function explainFailure(error: unknown): State {
  if (error instanceof StillWaking) {
    return {
      kind: "failed",
      canWake: true,
      message:
        `The server is taking longer than usual to wake up. I waited ` +
        `${String(Math.round(error.waitedMs / 1000))} seconds. It's a free instance, so this ` +
        `happens; another go usually catches it.`,
    };
  }
  if (error instanceof ApiError) {
    if (error.status === 503) {
      return {
        kind: "failed",
        status: 503,
        canWake: false,
        message:
          "The server's up but it can't reach its database. Waiting won't help with this one. " +
          "If you're seeing this, something is genuinely broken and I'd like to know.",
      };
    }
    if (isColdStart(error)) {
      return {
        kind: "failed",
        status: error.status,
        canWake: true,
        message: "The server didn't answer. It may still be starting up.",
      };
    }
    if (error.status === 429) {
      // Two different 429s reach here and they need different sentences.
      //
      // Ours is a problem document with `code: RATE_LIMITED` -- the per-route
      // limits on the credential endpoints, which are deliberately tight.
      //
      // The host's is not. Measured on 21 November through the rewrite:
      // `429`, `content-type: text/plain`, body `Too Many Requests`, and
      // **`x-render-routing: hibernate-rate-limited`** -- Render throttling
      // how often a sleeping free instance may be woken. Waiting is the only
      // thing that clears it, and offering a wake button would knock on the
      // origin again, which is what earned the throttle.
      const ours = error.code === "RATE_LIMITED";
      return {
        kind: "failed",
        status: 429,
        canWake: false,
        message: ours
          ? "You're going faster than this demo is allowed to answer. It's a free instance " +
            "with a small per-minute budget on the sign-in routes. Give it a few seconds."
          : "The host is throttling wake-ups for this free instance, because it has been woken " +
            "a lot recently. Nothing is broken and nothing you can press will help: it clears " +
            "on its own in a few minutes.",
      };
    }
    // The last branch, and it may not print `error.message` unframed -- that
    // is the whole lesson above. A problem document's title is worth showing
    // because the server wrote it for a human; its absence is itself worth
    // saying, because it means the answer came from something that is not
    // this API.
    const title = error.problem?.title;
    return {
      kind: "failed",
      status: error.status,
      canWake: false,
      message:
        title === undefined || title === ""
          ? "Something answered, and it was not this API -- no explanation came back in a form " +
            "I can read. If you are seeing this I would like to know."
          : `The server refused that: ${title}.`,
    };
  }
  return {
    kind: "failed",
    canWake: true,
    message: "I couldn't reach the server. Nothing answered at all.",
  };
}
