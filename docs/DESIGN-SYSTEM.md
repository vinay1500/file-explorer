# Design System

> **Product:** File Explorer · **Scope:** `apps/web` · **Last updated:** 2026-11-13
> **Status:** PROPOSED — tokens defined, not yet implemented.

A deliberately small system. The product is one screen containing a tree, a toolbar, two
dialogs and a breadcrumb trail. The purpose of this document is to stop arbitrary values
appearing in components, not to build a component library.

Every colour value below has had its contrast ratio computed against the surfaces it is used
on. The numbers in §3 are measured, not estimated.

---

## 1. Design principles

- **The tree is the product.** Everything else is chrome and stays quiet.
- **Dense but not cramped.** A file manager is scanned, not read. Rows are compact; the spacing
  scale is 4px so compactness stays consistent rather than improvised.
- **State must be visible without colour.** Selection, drag-over, loading, error and disabled
  each carry a non-colour cue as well — a border, a weight change, an icon, a position.
- **No arbitrary values in components.** Every colour, space, radius and duration comes from a
  token in `src/styles/tokens.css`. A raw hex in a component is a review failure.
- **Reuse before creating.** Two near-identical components is the failure mode this document
  exists to prevent.

---

## 2. Brand direction

- **Personality:** quiet, precise, utilitarian. It should feel like a tool, not a product tour.
- **Reference points:** the file panes of macOS Finder and VS Code's explorer — flat surfaces,
  one accent, restraint with iconography.
- **Avoid:** gradients, decorative shadows, coloured headers, animated page transitions,
  emoji as interface iconography. The current prototype uses 📁 🗃️ 🗑️ as its entire icon set;
  these are replaced with a consistent SVG icon set, because emoji render differently on every
  platform and carry inconsistent accessible names.

---

## 3. Colour tokens

Defined once in `src/styles/tokens.css` on `:root`, redefined for dark under both
`@media (prefers-color-scheme: dark)` and `[data-theme="dark"]`.

| Token | Light | Dark | Usage |
|---|---|---|---|
| `--color-bg` | `#FFFFFF` | `#0D1117` | Page background |
| `--color-surface` | `#F6F8FA` | `#161B22` | Toolbar, dialog, tree panel |
| `--color-surface-raised` | `#FFFFFF` | `#1C2128` | Dialog above surface, drag preview |
| `--color-text` | `#1F2328` | `#E6EDF3` | Primary text, node names |
| `--color-muted` | `#59636E` | `#9198A1` | Secondary text, breadcrumb separators, counts |
| `--color-primary` | `#0969DA` | `#4493F8` | Links, primary action, focus ring |
| `--color-border` | `#D1D9E0` | `#3D444D` | Dividers and decorative rules |
| `--color-border-interactive` | `#818B98` | `#6E7681` | Input, button and control outlines |
| `--color-danger` | `#CF222E` | `#F85149` | Errors, delete action |
| `--color-success` | `#1A7F37` | `#3FB950` | Success confirmations |
| `--color-selected-bg` | `#DDF4FF` | `#0C2D6B` | Selected row background |
| `--color-dropzone-bg` | `#DDF4FF` | `#0C2D6B` | Valid drop target (plus a 2px `--color-primary` outline) |
| `--color-dropzone-invalid` | `#FFEBE9` | `#2D1214` | Rejected drop target (plus a `--color-danger` outline) |

### Measured contrast

| Pair | Light | Dark | Requirement |
|---|---|---|---|
| text on bg | **15.80:1** | **16.02:1** | ≥ 4.5 (AA body) |
| text on surface | **14.84:1** | **14.64:1** | ≥ 4.5 |
| muted on bg | **6.11:1** | **6.50:1** | ≥ 4.5 |
| muted on surface | **5.74:1** | **5.94:1** | ≥ 4.5 |
| primary on bg | **5.19:1** | **6.11:1** | ≥ 4.5 |
| danger on bg | **5.36:1** | **5.65:1** | ≥ 4.5 |
| success on bg | **5.08:1** | **7.45:1** | ≥ 4.5 |
| text on selected row | **13.89:1** | **11.08:1** | ≥ 4.5 |
| interactive border on bg | **3.45:1** | **4.12:1** | ≥ 3.0 (AA non-text) |
| interactive border on surface | **3.24:1** | **3.77:1** | ≥ 3.0 |
| focus ring on bg | **5.19:1** | **6.11:1** | ≥ 3.0 |

`--color-border` is deliberately below 3:1. It is decorative only — it separates rows but never
communicates a boundary the user must perceive. Anything a user interacts with uses
`--color-border-interactive`.

---

## 4. Typography

| Token | Value |
|---|---|
| `--font-ui` | `system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif` |
| `--font-mono` | `ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace` |

The system UI font is used deliberately: a file manager should look native to the machine it is
running on. `--font-mono` is for node ids, API paths and anything in the error detail panel.

| Token | Size | Line height | Used for |
|---|---|---|---|
| `--text-xs` | 11px | 16px | Counts, timestamps, keyboard hints |
| `--text-sm` | 12px | 18px | Breadcrumbs, helper text, dialog labels |
| `--text-base` | 14px | 20px | Node names, body, buttons |
| `--text-lg` | 16px | 24px | Dialog titles |
| `--text-xl` | 20px | 28px | Page title |

Weights: 400 body, 500 selected node name and button labels, 600 dialog titles. No other
weights. No italic. No letter-spacing except on the one uppercase label style
(`--text-xs`, `0.08em`).

---

## 5. Spacing, sizing and radius

Base unit **4px**. Every margin, padding and gap is a multiple.

| Token | Value |
|---|---|
| `--space-1` … `--space-8` | 4, 8, 12, 16, 20, 24, 32, 40 px |
| `--row-height` | 28px (the tree row — the single most repeated measurement) |
| `--indent-step` | 16px per depth level |
| `--icon-size` | 16px |
| `--hit-target-min` | 32px (pointer), 44px (coarse pointer) |
| `--radius-sm` | 3px — rows, inputs, buttons |
| `--radius-md` | 6px — dialogs, panels |
| `--content-max` | 960px |
| `--rail-width` | 280px minimum, resizable |

Breakpoints: `--bp-sm` 640px, `--bp-md` 900px, `--bp-lg` 1200px.

Indentation is `calc(var(--indent-step) * depth)` applied as padding, not margin, so the row's
hover and selection background still spans the full width at every depth. The current prototype
uses `marginLeft: depth * 20`, which leaves the highlight starting part-way across the row.

---

## 6. Components

### Tree row
The core component. States, each with a non-colour cue:

| State | Treatment |
|---|---|
| Default | `--color-text`, transparent background |
| Hover | `--color-surface` background |
| Selected | `--color-selected-bg`, name at weight 500 |
| Focused | 2px `--color-primary` focus ring, inset |
| Dragging | 50% opacity, cursor `grabbing` |
| Valid drop target | `--color-dropzone-bg` + 2px `--color-primary` outline |
| Invalid drop target | `--color-dropzone-invalid` + 2px `--color-danger` outline + `not-allowed` cursor |
| Renaming | Becomes a real `<input>`, not `contentEditable` |
| Pending (optimistic) | Name at 60% opacity with a small inline spinner |
| Failed | 2px `--color-danger` left border, retry affordance in the row |

**Invalid drop must be shown, not silently ignored.** The current prototype refuses some drops
with no feedback at all, which reads as the application being broken.

**Rename uses an `<input>`.** The prototype's `contentEditable` span accepts pasted rich
content, emits unvalidated `textContent`, and has no accessible name.

### Buttons
Primary, secondary, tertiary, destructive — each with default, hover, focus-visible, active,
disabled and loading. A loading button keeps its width and stays disabled. A destructive action
on a folder requires confirmation naming the folder and the number of items inside it.

### Forms
Visible labels, never placeholder-as-label. Required fields marked in text, not by colour.
Validation messages sit beneath the field, are referenced by `aria-describedby`, and say what to
do. Input is preserved after a failed submit.

### Dialogs
Native `<dialog>`. Accessible title referenced by `aria-labelledby`, focus trapped, focus
restored to the trigger on close, Escape closes, an explicit Cancel alongside the primary action.

### Feedback
Every mutation ends in a visible outcome. A failed request shows an inline error with the
Problem Details `title` and a retry affordance. `console.error` alone is a bug.

| State | Treatment |
|---|---|
| Loading (first) | Skeleton rows at `--row-height`, no spinner-on-blank-screen |
| Loading (refetch) | Existing data stays; a thin progress bar at the panel's top edge |
| Empty | "This folder is empty" plus a create action |
| Error | Inline panel: what failed, why, what to do, retry |
| Offline / API unreachable | Persistent banner, not a toast |

---

## 7. Motion

| Token | Value | Used for |
|---|---|---|
| `--motion-fast` | 120ms | Hover, selection |
| `--motion-base` | 180ms | Expand and collapse, dialog entry |
| `--ease` | `cubic-bezier(0.2, 0, 0.2, 1)` | Everything |

Animation is used only to explain a state change — a folder expanding, a dialog arriving, a row
settling after a move. Nothing animates on page load. All of it is wrapped in
`@media (prefers-reduced-motion: reduce)`, which reduces durations to zero rather than removing
the state change.

---

## 8. Accessibility

Target: **WCAG 2.2 AA**. This is a testable claim, so it is tested — `make a11y` runs
axe-core against the running app in CI, and the Playwright suite drives the tree by keyboard
only. A target that is not checked is not kept.

- The tree is a real `role="tree"` with `role="treeitem"`, `aria-expanded`, `aria-level`,
  `aria-selected`. Arrow keys navigate, Right/Left expand and collapse, Enter renames,
  Delete removes, Escape cancels.
- **Drag-and-drop has a keyboard equivalent.** Cut-and-paste style: select, `Ctrl/Cmd+X`,
  navigate to destination, `Ctrl/Cmd+V`. Drag-only is a failure at AA.
- Every icon-only control has an accessible name. Folder and file are distinguished by icon
  *and* by an `aria-label`, never by colour.
- Visible focus on every interactive element, never `outline: none` without a replacement.
- Live region announces the outcome of each mutation ("Moved Reports into Archive").
- Contrast as measured in §3.

---

## 9. Review checklist

- [ ] Every colour, space, radius and duration came from a token.
- [ ] Hover, focus-visible, active, disabled and loading all handled.
- [ ] Loading, empty and error states exist for anything that fetches.
- [ ] Keyboard path works end to end, including moving a node.
- [ ] Meaning is never carried by colour alone.
- [ ] Checked at 640px and at 1200px; no horizontal page scroll.
- [ ] Both themes checked, including the un-stamped system-default state.
- [ ] `make a11y` passes.
