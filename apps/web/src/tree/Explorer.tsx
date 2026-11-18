import { useCallback, useEffect, useRef, useState } from "react";
import { DndProvider } from "react-dnd";
import { HTML5Backend } from "react-dnd-html5-backend";
import { Tree, type DropOptions, type NodeModel } from "@minoru/react-dnd-treeview";
import { ApiError } from "../api/client";
import { createNode, deleteNode, listChildren, listRoots, moveNode, renameNode } from "../api/nodes";
import { ROOT_ID, mergeNodes, subtreeIds, toParentId, toTreeNode, type NodeData } from "./model";

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
 * The client guard is best-effort anyway: children load on expand, so
 * `isAncestor` only sees what has been loaded. **The server is authoritative**
 * -- it answers 409 `CYCLE_DETECTED` from a check inside a row-locked
 * transaction -- and this component shows that answer rather than hiding it.
 */
export function Explorer() {
  const [nodes, setNodes] = useState<Tn[]>([]);
  const [loaded, setLoaded] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [renaming, setRenaming] = useState<Tn | null>(null);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState<Tn | null>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);

  const report = useCallback((e: unknown) => {
    setNotice(null);
    setError(e instanceof ApiError ? e.message : "The request did not reach the server.");
  }, []);

  const loadRoots = useCallback(async () => {
    try {
      const items = await listRoots();
      setNodes((current) => mergeNodes(current, items.map(toTreeNode)));
      setError(null);
    } catch (e: unknown) {
      report(e);
    }
  }, [report]);

  useEffect(() => {
    void loadRoots();
  }, [loadRoots]);

  // Move focus into the confirmation when it opens. Without this a keyboard
  // user tabs straight past Delete and Cancel back into the row buttons.
  useEffect(() => {
    if (confirming !== null) confirmRef.current?.focus();
  }, [confirming]);

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

  const handleDrop = async (_tree: Tn[], options: DropOptions<NodeData>) => {
    const sourceId = options.dragSourceId;
    if (sourceId === undefined) return;
    const source = nodes.find((n) => n.id === sourceId);
    if (!source) return;

    const from = toParentId(source.parent);
    const to = toParentId(options.dropTargetId);
    if (from === to) return;

    setBusy(true);
    try {
      // `to` is `null` when the destination is the root. The old client looked
      // the target up by id, found nothing for the root, and returned early --
      // which is F-06, and is why nothing could ever be dragged back out.
      await moveNode(typeof sourceId === "number" ? sourceId : Number(sourceId), to);
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
        setNotice(`Deleted ${String(result.deleted)} items.`);
      }
    } catch (e: unknown) {
      report(e);
    } finally {
      setBusy(false);
    }
  };

  /** Create a folder at the root. The only way to add anything to the tree. */
  const addFolder = async () => {
    const name = window.prompt("Name for the new folder");
    if (name === null || name.trim() === "") return;
    setBusy(true);
    try {
      await createNode(name.trim(), "folder", null);
      setError(null);
      await loadRoots();
    } catch (e: unknown) {
      report(e);
    } finally {
      setBusy(false);
    }
  };

  const descendantCount = confirming ? subtreeIds(nodes, confirming.id).size - 1 : 0;

  return (
    <section className="explorer" aria-busy={busy}>
      {error !== null && (
        <p className="note bad" role="alert">
          {error}
        </p>
      )}
      {notice !== null && (
        <p className="note ok" role="status">
          {notice}
        </p>
      )}

      <p className="toolbar">
        <button type="button" onClick={() => void addFolder()} disabled={busy}>
          New folder
        </button>
      </p>

      <DndProvider backend={HTML5Backend}>
        <Tree<NodeData>
          tree={nodes}
          rootId={ROOT_ID}
          onDrop={(tree, options) => void handleDrop(tree, options)}
          onChangeOpen={(openIds) => void openChanged(openIds)}
          classes={{ root: "tree-root", dropTarget: "tree-drop", draggingSource: "tree-dragging" }}
          render={(node, { depth, isOpen, onToggle }) => (
            <div className="row" style={{ paddingInlineStart: `${String(depth * 18)}px` }}>
              {node.droppable === true ? (
                <button
                  type="button"
                  className="twist"
                  aria-expanded={isOpen}
                  aria-label={isOpen ? `Collapse ${node.text}` : `Expand ${node.text}`}
                  onClick={onToggle}
                >
                  {isOpen ? "▾" : "▸"}
                </button>
              ) : (
                <span className="twist" aria-hidden="true" />
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
                <button
                  type="button"
                  onClick={() => {
                    setRenaming(node);
                    setDraft(node.text);
                  }}
                  aria-label={`Rename ${node.text}`}
                >
                  Rename
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(node)}
                  aria-label={`Delete ${node.text}`}
                >
                  Delete
                </button>
              </span>
            </div>
          )}
        />
      </DndProvider>

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
          onKeyDown={(e) => {
            if (e.key === "Escape") setConfirming(null);
          }}>
          <p>
            Delete <strong>{confirming.text}</strong>
            {descendantCount > 0 && (
              <>
                {" "}
                and <strong>{descendantCount}</strong> item
                {descendantCount === 1 ? "" : "s"} inside it
              </>
            )}
            ?
          </p>
          <p className="muted">This cannot be undone.</p>
          <button type="button" ref={confirmRef} onClick={() => void commitDelete()} disabled={busy}>
            Delete
          </button>
          <button type="button" className="ghost" onClick={() => setConfirming(null)}>
            Cancel
          </button>
        </div>
      )}
    </section>
  );
}
