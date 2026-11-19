import { useCallback, useEffect, useRef, useState } from "react";
import { DndProvider } from "react-dnd";
import { HTML5Backend } from "react-dnd-html5-backend";
import { Tree, type DropOptions, type NodeModel } from "@minoru/react-dnd-treeview";
import { ApiError } from "../api/client";
import { createNode, deleteNode, listChildren, listRoots, moveNode, renameNode } from "../api/nodes";
import { isColdStart, wakeApi } from "../api/wake";
import {
  BinGlyph,
  Chevron,
  FileGlyph,
  FolderClosed,
  FolderOpen,
  PencilGlyph,
  UpToRootGlyph,
} from "../ui/icons";
import { kindOf } from "../ui/fileKind";
import {
  ROOT_ID,
  mergeNodes,
  subtreeFullyLoaded,
  subtreeIds,
  toParentId,
  toTreeNode,
  type NodeData,
} from "./model";

type Tn = NodeModel<NodeData>;

/**
 * The tree.
 *
 * **There is deliberately no `canDrop` prop on `<Tree>`, and that is the most
 * important line in this file.**
 *
 * Read from the pinned version's own bundle (3.5.4), the library's drop rule
 * is:
 *
 * ```js
 * if (canDrop) {
 *   const result = canDrop(dragSourceId, dropTargetId);
 *   if (result !== undefined) return result;   // <- replaces everything below
 * }
 * if (dragSourceId === dropTargetId) return false;
 * if (dropTargetNode === undefined) return dragSourceNode.parent !== 0;   // the root
 * if (dragSourceNode.parent === dropTargetId || !dropTargetNode.droppable) return false;
 * return !isAncestor(tree, dragSourceId, dropTargetId);
 * ```
 *
 * A custom callback that returns anything but `undefined` **replaces all four
 * rules** -- not only the ancestor check: also self-drop, also same-parent,
 * and also the rule that permits a drop on the root. The April 2025 client
 * passed `canDrop={(_, { dropTarget }) => dropTarget?.droppable}`, which for
 * any folder returned `true` and switched off every one of them. That is
 * F-37, and it is why dragging a folder into its own descendant has been
 * permitted since April 2025.
 *
 * The rule this file follows: **the safest custom `canDrop` is none.** The
 * only thing it would have added is "a file cannot be a destination", and the
 * library already does that from `droppable`, which `toTreeNode` sets. If one
 * is ever needed it must return `undefined` for every case it does not
 * actively reject, or it silently takes ownership of correctness it does not
 * implement.
 *
 * ---
 *
 * **F-62: the root rule above was reachable by a test and by nobody else.**
 *
 * `dropTargetNode === undefined` means the pointer is over the tree's own
 * container rather than over a row, and that is how a drag says "the root".
 * The container is a `<ul>` whose height is exactly the sum of its rows -- so
 * every pixel of it was covered by a row, and a drag could never produce that
 * condition. Measured on the deployed site: 1,368 sample points inside the
 * container, **zero** of which `document.elementFromPoint` resolved to the
 * container itself. The Playwright test passed because `dispatchEvent` names
 * its target directly and skips hit testing entirely.
 *
 * The fix is `padding-bottom` on that same container: **the padding box is
 * the hit area**, and a point inside it resolves to the `<ul>` itself. The
 * dashed band drawn there by `::after` is paint and nothing else -- it
 * carries `pointer-events: none`, and deleting the whole rule leaves the drop
 * working and all three browser tests green. It is there so a person can see
 * where to aim, which is a different job from being the target, and an
 * earlier version of this comment credited it with both. Not a child
 * element: a child would be the topmost target under the pointer, and
 * react-dnd would have to bubble through it.
 *
 * Every row that is not already at the top level also carries a **Move to top
 * level** button, because a drag is not an affordance for a keyboard, and
 * because the one gesture this whole project is named after should not be
 * reachable only by mouse.
 *
 * The client guard is best-effort anyway: children load on expand, so
 * `isAncestor` only sees what has been loaded. **The server is authoritative**
 * -- it answers 409 `CYCLE_DETECTED` from a check inside a row-locked
 * transaction -- and this component shows that answer rather than hiding it.
 */
export function Explorer() {
  const [nodes, setNodes] = useState<Tn[]>([]);
  const [loaded, setLoaded] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [asleep, setAsleep] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [renaming, setRenaming] = useState<Tn | null>(null);
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [confirming, setConfirming] = useState<Tn | null>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const returnFocusTo = useRef<HTMLElement | null>(null);

  const report = useCallback((e: unknown) => {
    setNotice(null);
    // A cold start is not a failure of the request, it is the absence of a
    // server to make it to. Saying "request failed with 502" for it is what
    // the first version did, and it reads as a broken application.
    if (isColdStart(e)) {
      setAsleep(true);
      setError("The server went back to sleep between requests.");
      return;
    }
    setAsleep(false);
    setError(e instanceof ApiError ? e.message : "That did not reach the server.");
  }, []);

  const loadRoots = useCallback(async () => {
    try {
      const items = await listRoots();
      setNodes((current) => mergeNodes(current, items.map(toTreeNode)));
      setError(null);
      setAsleep(false);
    } catch (e: unknown) {
      report(e);
    }
  }, [report]);

  useEffect(() => {
    void loadRoots();
  }, [loadRoots]);

  // Move focus into the confirmation when it opens, and remember where it
  // came from. Without the first a keyboard user tabs straight past Delete
  // and Cancel back into the row buttons; without the second, cancelling
  // drops focus onto `<body>` and the next Tab starts again from the top of
  // the page -- which, in a tree of forty rows, loses your place entirely.
  useEffect(() => {
    if (confirming === null) return;
    returnFocusTo.current = document.activeElement as HTMLElement | null;
    confirmRef.current?.focus();
  }, [confirming]);

  const dismissConfirm = useCallback(() => {
    setConfirming(null);
    returnFocusTo.current?.focus();
    returnFocusTo.current = null;
  }, []);

  const wakeAndReload = async () => {
    setBusy(true);
    setError("Waking it up.");
    try {
      await wakeApi();
      setAsleep(false);
      setError(null);
      await loadRoots();
    } catch (e: unknown) {
      report(e);
    } finally {
      setBusy(false);
    }
  };

  /**
   * Children load when a folder is first opened, through `onChangeOpen` --
   * the library has no `onToggle` prop, and the per-node `onToggle` in
   * `RenderParams` only flips open state. `onChangeOpen` hands back the full
   * set of open ids, so this loads any that have not been loaded before and
   * ignores the rest.
   */
  const openChanged = useCallback(
    async (openIds: NodeModel["id"][]) => {
      for (const id of openIds) {
        const numeric = typeof id === "number" ? id : Number(id);
        if (loaded.has(numeric)) continue;
        try {
          const items = await listChildren(numeric);
          setNodes((current) => mergeNodes(current, items.map(toTreeNode)));
          // Marked loaded only after the request succeeds. Marking it first
          // meant one 503 left the folder permanently empty for the session:
          // `loaded` is the guard against re-fetching, so a failed load looked
          // exactly like a folder with no children, for ever.
          setLoaded((current) => new Set(current).add(numeric));
          setError(null);
        } catch (e: unknown) {
          report(e);
        }
      }
    },
    [loaded, report],
  );

  /**
   * After every mutation the affected parents are reloaded rather than patched
   * in place. D-23 dropped optimistic updates on purpose: their failure mode
   * is showing a tree the server does not have, which is the same class of bug
   * this project is named after.
   *
   * A refetch is slower, and it only cannot lie if it sees everything. The
   * first version of this comment said "cannot lie" while nothing in the
   * client read `next_cursor` and the server pages at fifty -- so with fifty
   * roots sorting before it, a node moved to the root would have stayed drawn
   * inside the folder it had just left. `listRoots` and `listChildren` follow
   * pages now, which is what makes the claim true rather than hopeful.
   */
  const reload = useCallback(
    async (parents: (number | null)[]) => {
      await loadRoots();
      for (const parent of parents) {
        if (parent === null) continue;
        try {
          const items = await listChildren(parent);
          setNodes((current) => mergeNodes(current, items.map(toTreeNode)));
        } catch (e: unknown) {
          report(e);
        }
      }
    },
    [loadRoots, report],
  );

  const move = async (id: number, from: number | null, to: number | null) => {
    if (from === to) return;
    setBusy(true);
    try {
      // `to` is `null` when the destination is the root. The old client looked
      // the target up by id, found nothing for the root, and returned early --
      // which is F-06, and is why nothing could ever be dragged back out.
      await moveNode(id, to);
      setError(null);
      await reload([from, to]);
    } catch (e: unknown) {
      report(e);
      // Reload anyway. A refused move leaves the server's tree unchanged, and
      // re-reading it is what puts the row back where it belongs on screen.
      await reload([from, to]);
    } finally {
      setBusy(false);
    }
  };

  const handleDrop = async (_tree: Tn[], options: DropOptions<NodeData>) => {
    const sourceId = options.dragSourceId;
    if (sourceId === undefined) return;
    const source = nodes.find((n) => n.id === sourceId);
    if (!source) return;
    const id = typeof sourceId === "number" ? sourceId : Number(sourceId);
    await move(id, toParentId(source.parent), toParentId(options.dropTargetId));
  };

  const commitRename = async () => {
    if (!renaming) return;
    const id = typeof renaming.id === "number" ? renaming.id : Number(renaming.id);
    const name = draft.trim();
    if (name === "" || name === renaming.text) {
      setRenaming(null);
      return;
    }
    setBusy(true);
    try {
      await renameNode(id, name);
      setError(null);
      setRenaming(null);
      await reload([toParentId(renaming.parent)]);
    } catch (e: unknown) {
      report(e);
    } finally {
      setBusy(false);
    }
  };

  const commitDelete = async () => {
    if (!confirming) return;
    const id = typeof confirming.id === "number" ? confirming.id : Number(confirming.id);
    const parent = toParentId(confirming.parent);
    setBusy(true);
    try {
      const result = await deleteNode(id);
      setError(null);
      const gone = subtreeIds(nodes, confirming.id);
      setNodes((current) => current.filter((n) => !gone.has(n.id)));
      setConfirming(null);
      await reload([parent]);
      if ((result.deleted ?? 0) > 1) {
        // Through the notice channel, not `setError`. It was announced with
        // role="alert" in red, which tells a screen-reader user that deleting
        // the thing they asked to delete went wrong.
        setNotice(`Deleted ${String(result.deleted)} things.`);
      }
    } catch (e: unknown) {
      report(e);
    } finally {
      setBusy(false);
    }
  };

  /**
   * Create a folder at the top level.
   *
   * It was a `window.prompt` until M8. That is one line of code and three
   * problems: Chrome suppresses it outright after a few uses on a page,
   * Firefox lets the user tick "prevent further dialogs" and then nothing
   * happens at all, and neither can be styled, focused or cancelled with a
   * keyboard in the way the rest of this component can.
   */
  const commitCreate = async () => {
    const name = newName.trim();
    if (name === "") {
      setAdding(false);
      return;
    }
    setBusy(true);
    try {
      await createNode(name, "folder", null);
      setError(null);
      setAdding(false);
      setNewName("");
      await loadRoots();
    } catch (e: unknown) {
      report(e);
    } finally {
      setBusy(false);
    }
  };

  const descendantCount = confirming ? subtreeIds(nodes, confirming.id).size - 1 : 0;
  // Whether that number means anything. A collapsed folder's children have
  // never been fetched, so the count is zero and the dialog would quietly
  // promise that nothing is inside.
  const countIsKnown = confirming !== null && subtreeFullyLoaded(nodes, loaded, confirming.id);
  const rootCount = nodes.filter((n) => n.parent === ROOT_ID).length;

  return (
    <section className="explorer panel" aria-busy={busy}>
      <div className="ex-head">
        <h2 className="panel-h">Your folders</h2>
        <div className="ex-actions">
          <button
            type="button"
            className="primary small"
            onClick={() => {
              setAdding(true);
              setNewName("");
            }}
            disabled={busy || adding}
          >
            New folder
          </button>
        </div>
      </div>

      {error !== null && (
        <p className="note bad" role="alert">
          {error}
          {asleep && (
            <button type="button" className="link" onClick={() => void wakeAndReload()} disabled={busy}>
              Wake it
            </button>
          )}
        </p>
      )}
      {notice !== null && (
        <p className="note ok" role="status">
          {notice}
        </p>
      )}

      {adding && (
        <form
          className="new-row"
          onSubmit={(e) => {
            e.preventDefault();
            void commitCreate();
          }}
        >
          <FolderClosed className="ic ic-folder" />
          <input
            autoFocus
            className="rename"
            aria-label="Name for the new folder"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setAdding(false);
            }}
          />
          <button type="submit" className="primary small" disabled={busy}>
            Create
          </button>
          <button type="button" className="ghost small" onClick={() => setAdding(false)}>
            Cancel
          </button>
        </form>
      )}

      <DndProvider backend={HTML5Backend}>
        <Tree<NodeData>
          tree={nodes}
          rootId={ROOT_ID}
          onDrop={(tree, options) => void handleDrop(tree, options)}
          onChangeOpen={(openIds) => void openChanged(openIds)}
          classes={{ root: "tree-root", dropTarget: "tree-drop", draggingSource: "tree-dragging" }}
          render={(node, { depth, isOpen, onToggle }) => {
            const isFolder = node.droppable === true;
            const atTop = node.parent === ROOT_ID;
            return (
              <div
                className={`row ${isFolder ? "is-folder" : "is-file"} ${depth > 0 ? "indented" : ""}`}
                // `--depth` draws the indent guide; the padding does the
                // indenting itself. Both come from the same number, so a
                // guide cannot drift away from the row it belongs to.
                style={
                  {
                    paddingInlineStart: `calc(${String(depth)} * var(--step))`,
                    "--depth": depth,
                  } as React.CSSProperties
                }
              >
                {isFolder ? (
                  <button
                    type="button"
                    className={`twist ${isOpen ? "open" : ""}`}
                    aria-expanded={isOpen}
                    aria-label={isOpen ? `Collapse ${node.text}` : `Expand ${node.text}`}
                    onClick={onToggle}
                  >
                    <Chevron />
                  </button>
                ) : (
                  <span className="twist" aria-hidden="true" />
                )}

                {isFolder ? (
                  isOpen ? (
                    <FolderOpen className="ic ic-folder" />
                  ) : (
                    <FolderClosed className="ic ic-folder" />
                  )
                ) : (
                  <FileGlyph className={`ic ic-file k-${kindOf(node.text)}`} />
                )}

                {renaming?.id === node.id ? (
                  <input
                    className="rename"
                    autoFocus
                    value={draft}
                    aria-label={`New name for ${node.text}`}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={() => void commitRename()}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void commitRename();
                      if (e.key === "Escape") setRenaming(null);
                    }}
                  />
                ) : (
                  <span className="name">{node.text}</span>
                )}

                <span className="actions">
                  {!atTop && (
                    <button
                      type="button"
                      className="icon-btn"
                      onClick={() =>
                        void move(
                          typeof node.id === "number" ? node.id : Number(node.id),
                          toParentId(node.parent),
                          null,
                        )
                      }
                      aria-label={`Move ${node.text} to the top level`}
                      title="Move to the top level"
                    >
                      <UpToRootGlyph />
                    </button>
                  )}
                  <button
                    type="button"
                    className="icon-btn"
                    onClick={() => {
                      setRenaming(node);
                      setDraft(node.text);
                    }}
                    aria-label={`Rename ${node.text}`}
                    title="Rename"
                  >
                    <PencilGlyph />
                  </button>
                  <button
                    type="button"
                    className="icon-btn danger"
                    onClick={() => setConfirming(node)}
                    aria-label={`Delete ${node.text}`}
                    title="Delete"
                  >
                    <BinGlyph />
                  </button>
                </span>
              </div>
            );
          }}
        />
      </DndProvider>

      {rootCount === 0 && !busy && (
        <p className="empty">
          Nothing in here yet. Use <strong>New folder</strong> to make one.
        </p>
      )}

      <div className="howto">
        <h3>What you can do here</h3>
        <ol>
          <li>
            <strong>Open a folder</strong> by clicking the arrow beside it. Its contents load the
            first time you open it.
          </li>
          <li>
            <strong>Move something into a folder</strong> by dragging it onto that folder.
          </li>
          <li>
            <strong>Move something back to the top level</strong> by dragging it onto the dashed
            band under the tree, or by clicking <UpToRootGlyph className="ic inline" /> on its
            row. The button does the same thing without a drag, which matters if you are using a
            keyboard.
          </li>
          <li>
            <strong>Rename or delete</strong> with the two buttons that appear when you hover a
            row. Deleting takes everything inside with it, and asks first.
          </li>
          <li>
            <strong>Try to drop a folder inside itself.</strong> It will not let you, and the
            server is the one that refuses: it answers 409 and says which rule you hit. That bug
            is the reason this project exists.
          </li>
        </ol>
      </div>

      {/*
        The delete confirmation, which the April 2025 client did not have at
        all: one click on a bin icon destroyed a folder -- and, because the
        schema had no cascade, scattered its contents to the root. The cascade
        exists now, so the question is no longer "what happens to the
        children" but "do you know how many there are".
      */}
      {/*
        `role="alertdialog"` without `aria-modal`. The attribute tells assistive
        technology the rest of the page is inert -- and it is not: there is no
        focus trap here and the tree behind stays reachable. Claiming modality
        that does not exist is worse for a screen-reader user than omitting it,
        because content they can still reach is hidden from them. Focus moves
        to the destructive button on open and Escape cancels, which is the part
        that is actually implemented.
      */}
      {confirming !== null && (
        <div className="confirm" role="alertdialog" aria-label="Confirm delete"
          aria-describedby="confirm-q"
          onKeyDown={(e) => {
            if (e.key === "Escape") dismissConfirm();
          }}>
          <p id="confirm-q">
            Delete <strong>{confirming.text}</strong>
            {countIsKnown ? (
              descendantCount > 0 && (
                <>
                  {" "}
                  and <strong>{descendantCount}</strong> item
                  {descendantCount === 1 ? "" : "s"} inside it
                </>
              )
            ) : (
              <>
                {" "}
                and <strong>everything inside it</strong>
              </>
            )}
            ?
          </p>
          {!countIsKnown && (
            <p className="muted">
              I have not loaded what is inside this folder, so I cannot tell you how many
              things there are. All of them go too, and you will see the count afterwards.
            </p>
          )}
          <p className="muted">You cannot undo this.</p>
          <button type="button" ref={confirmRef} className="danger" onClick={() => void commitDelete()} disabled={busy}>
            Delete
          </button>
          <button type="button" className="ghost" onClick={dismissConfirm}>
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}
