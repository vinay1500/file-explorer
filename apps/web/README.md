# apps/web

The React frontend. Renders the tree, handles drag-and-drop, and talks to `apps/api`.

> **Being rebuilt.** This is still the original Create React App code from April 2025. It is
> replaced with Vite and TypeScript at M6, at which point the API types are generated from the
> backend's OpenAPI document rather than matched by hand.

## Known problems in the current code

Carried over deliberately — they are the reason for the rebuild, and each has a fix scheduled:

- **A node can never be moved back to the root.** `handleDrop` looks the drop target up by id
  and returns early when it is missing; the root's id is `null`, so that lookup always fails.
  Once an item is inside a folder, the interface cannot get it out.
- **Failed requests are invisible.** Errors reach `console.error` and nothing else, so a failure
  looks identical to nothing happening.
- **Rename accepts anything.** An inline `contentEditable` sends raw `textContent` with no trim,
  no length cap and no validation.
- **Delete has no confirmation.** One click destroys a folder.
- **The API hostname is compiled in.** No environment variable, so this cannot be pointed at a
  local backend without editing source.
- **Dead code.** `FolderNode.jsx` and `FileNode.jsx` are never imported.

See [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) for what replaces this, and
[docs/DESIGN-SYSTEM.md](../../docs/DESIGN-SYSTEM.md) for the tokens and component states it will
be built against.
