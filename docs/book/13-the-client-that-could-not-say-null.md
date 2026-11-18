# Chapter 13 · The client that could not say null

> **Volume III · The rebuild.** The frontend was the last thing rebuilt and the first thing
> anyone sees. This chapter is about one value — `null` — and the four separate places that
> failing to express it broke something.

The planned title for this chapter was *Making it fast*. It was going to be about optimistic
updates: apply the change on screen immediately, send the request, roll back if the server
disagrees. That chapter was never written, because the feature was cut at
[decision D-23](../adr/) — its failure mode is **showing a tree the server does not have**,
which is precisely the class of bug this whole project is named after. Trading correctness for
perceived speed, in a rebuild whose entire subject is a tree that silently stopped matching
reality, would have been a joke told at my own expense.

So this is what the milestone was actually about.

---

## 1. Four rules, and a callback that switched off all of them

The April 2025 client used `@minoru/react-dnd-treeview`. It passed one prop:

```jsx
canDrop={(_tree, { dropTarget }) => dropTarget?.droppable}
```

That reads as a tightening. *Only allow drops onto things marked droppable.* It is the opposite.

Here is the library's drop rule, read from the compiled bundle of the exact version
`package-lock.json` pins:

```js
if (canDrop) {
  const result = canDrop(dragSourceId, dropTargetId);
  if (result !== undefined) {
    return result;                                    // ← everything below is skipped
  }
}
if (dragSourceId === dropTargetId) return false;                        // 1. self
if (dropTargetNode === undefined) return dragSourceNode.parent !== 0;   // 2. the root
if (dragSourceNode.parent === dropTargetId || !dropTargetNode.droppable) return false;  // 3.
return !isAncestor(tree, dragSourceId, dropTargetId);                   // 4. the cycle
```

A custom callback that returns anything other than `undefined` **replaces all four rules**. For
every folder, `dropTarget?.droppable` is `true`. So the client did not merely fail to add a
guard against dragging a folder into its own descendant — **it disabled the one the library
already had**, along with the self-drop check, the same-parent check and the root rule.

That is finding F-37, and it is the drag that produced the bug this project is named after.

The fix is not a better callback. **The fix is no callback.** The only thing the custom one was
reaching for — *a file cannot be a destination* — is rule 3, which the library already applies
from `droppable`. Set that correctly in the model and there is nothing left to add:

```ts
droppable: type === "folder",
```

A guard like this cannot be protected by a behavioural test, and that is worth sitting with. A
custom `canDrop` that replaces the rules **passes every test you would think to write**: folder
onto folder works, folder onto file is refused. What it switches off are the edge cases, and the
edge cases are the bugs. The client passed eighteen months of manual use with the cycle drag
wide open. So the test that guards it reads the source and asserts no `canDrop=` appears —
along with asserting the *explanation* is still next to the code, because a guard whose reason
is not written beside it gets removed by somebody tidying up.

---

## 2. `0` is a node. `null` is the root.

The library identifies the root by a `parent` matching its `rootId`, which is `0`. The API says
`parent_id: null`.

Those are not interchangeable. `0` is a perfectly good node id. `null` is not a value the
library understands. **A client that does not translate between them cannot express
"move this to the root" at all** — and that is finding F-06, the one a visitor can see: once an
item was inside a folder, nothing could get it out.

The original `handleDrop` looked the destination up by id, found nothing for the root, and
returned early. The feature did not fail; it did not exist. No error, no console message,
nothing in a log. The user dragged a folder to the empty space, let go, and the row went back
where it was.

The translation now lives in two functions, in one file, and nowhere else:

```ts
export function toTreeNode(node: Node): NodeModel<NodeData> {
  return { id: node.id ?? 0, parent: node.parent_id ?? ROOT_ID, ... };
}

export function toParentId(parent: NodeModel["id"]): number | null {
  if (parent === ROOT_ID || parent === "0") return null;
  return typeof parent === "number" ? parent : Number(parent);
}
```

Four unit tests pin the round trip in both directions. The one that matters is
`toParentId(ROOT_ID)` returning `null` rather than `0`: if it returned `0`, the API would be
asked to re-parent the node *under node zero*, which is a 404 — not a move to the root — and
the feature would silently not exist again, in a new way.

---

## 3. The type that would have let it come back

The server has accepted `new_parent_id: null` since M2. The move algorithm has handled it under
a row lock since then. **F-06 was never a server bug**, which is why no backend test could have
caught it.

The frontend's types are generated from the API's own OpenAPI document, and CI regenerates them
and fails on any difference. That catches *drift*. It does not catch this:

> Someone narrows `new_parent_id` from `number | null` to `number`. The regenerated file
> contains the change. The diff is three lines. **It reads as tightening a type.** It is
> approved by anyone who does not already know that `null` is how a caller says "the root".

So the nullability is asserted in the type system rather than left to review:

```ts
export type MoveToRootIsExpressible = Assert<
  null extends NodeMove["new_parent_id"] ? true : false
>;
```

Narrow the document and the frontend stops compiling, with
`TS2344: Type 'false' does not satisfy the constraint 'true'`. That is
[mutation 21](../MUTATIONS.md), and it is the first guard in this project that reddens a build
rather than a test.

---

## 4. The browser test that asserts a request, not a picture

The end-to-end test drives a real drag in a real Chromium and then asserts:

```ts
expect(captured.bodies[0]).toEqual({ new_parent_id: null });
```

**Not the tree redrawing.** A test that checked the resulting tree would pass against a client
that sent `new_parent_id: 0`, received a 404, re-read the tree and found it unchanged — because
the tree *is* unchanged, which is exactly what a successful "it stayed where it was" looks like.
That is the shape of a test that reaches past the thing it is testing, and
[chapter 14](14-testing-what-matters.md) is about the five of those this project has already
found.

Two things made this harder than it should have been, and both are worth recording:

- **`page.mouse.down/move/up` does not drive an HTML5 drag.** The backend listens for the
  browser's native `dragstart`/`dragover`/`drop`, and Chromium only emits those for a genuine
  user gesture. Synthetic mouse movement produces a test that fails with *no request was made*,
  which reads like a broken application rather than a test driving the wrong events. The fix is
  to dispatch the drag events with one shared `DataTransfer`.
- **The complementary test first dropped a node onto its own parent**, which the library
  correctly refuses — so no request was made and the test failed for a reason that had nothing
  to do with what it checked. It drops onto a second root now and asserts the *id*, so the
  first test cannot pass against a client that sends `null` for everything.

---

## 5. The request that could never have arrived anyway

Even with the body right, the old client could not have worked, because it hardcoded the API's
absolute hostname:

```js
const api = axios.create({ baseURL: "https://file-explorer-backend.onrender.com/api" });
```

`*.vercel.app` and `*.onrender.com` are different sites under the Public Suffix List. A
`SameSite=Lax` session cookie is **refused at storage time** on a cross-site response that is not
a top-level navigation. So every authenticated request would have been anonymous, and the
failure would have looked like a backend bug.

The rebuild uses relative URLs only. The dev server proxies `/api/*`; Vercel rewrites it in
production; the browser only ever talks to one origin. The client *throws* on a non-relative
path rather than returning an error, because that is a programming mistake rather than a runtime
condition — and CI greps the built bundle for an `onrender.com` URL, because a relative URL and
an absolute one look identical in review.

---

## What this chapter is really about

Four defects, and not one of them is a mistake in logic. Every one is a **failure to express a
value**: a callback that said more than it meant, a root that two libraries named differently, a
type that could have stopped saying `null`, a URL that put the cookie out of reach.

The cheapest way to find this class of bug is to ask, of every boundary, *what is the value here
that cannot be written down?* For a tree, it is always the root — because the root is the one
node with no parent, and "no parent" is the thing every representation gets to invent its own
spelling for.

---

[← Chapter 12 · Identity and isolation](12-identity-and-isolation.md) ·
[Chapter 14 · Testing what matters →](14-testing-what-matters.md)
