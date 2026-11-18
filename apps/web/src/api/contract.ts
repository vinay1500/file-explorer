/**
 * The parts of the generated API surface this application relies on, plus
 * compile-time assertions that they still mean what they meant.
 *
 * `schema.d.ts` is generated from the API's own OpenAPI document and committed,
 * and CI regenerates it and fails on any difference. That catches *drift*: a
 * backend change that moves a field cannot reach the frontend unnoticed.
 *
 * Drift detection alone is not enough, and F-06 is why. The original bug was a
 * frontend that could not move an item back to the root. The root is
 * `parent_id: null`, so if the document ever types `new_parent_id` as a plain
 * integer, the generated client cannot express move-to-root at all -- the
 * frontend grows a workaround and the feature quietly stops working, exactly
 * as it did in April 2025. A regenerated file would *contain* that narrowing
 * and the diff would be approved by anyone not looking for it.
 *
 * So the nullability is asserted here, in the type system. Narrow the document
 * and this file stops compiling, which is a louder failure than a diff.
 */
import type { components } from "./schema";

export type Node = components["schemas"]["Node"];
export type NodeCreate = components["schemas"]["NodeCreate"];
export type NodeMove = components["schemas"]["NodeMove"];
export type NodeRename = components["schemas"]["NodeRename"];
export type Page = components["schemas"]["Page"];
export type Problem = components["schemas"]["Problem"];

/** Compiles only when `Condition` is exactly `true`. */
type Assert<Condition extends true> = Condition;

/**
 * F-06, at the type level: `null` must remain assignable to `new_parent_id`,
 * because `null` is how a caller says "the root".
 */
export type MoveToRootIsExpressible = Assert<
  null extends NodeMove["new_parent_id"] ? true : false
>;

/**
 * And the same for a node's own parent, which is what a tree uses to decide
 * whether a row is a root. A non-nullable `parent_id` makes roots
 * unrepresentable and sends the client looking for a sentinel.
 */
export type RootIsRepresentable = Assert<
  null extends Node["parent_id"] ? true : false
>;

/**
 * `parent_id` on create is nullable too -- that is how a root is made. The
 * three together mean the client can describe the whole tree, including its
 * edges, without inventing anything.
 */
export type RootIsCreatable = Assert<
  null extends NodeCreate["parent_id"] ? true : false
>;
