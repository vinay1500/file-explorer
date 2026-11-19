/**
 * Which colour a file's glyph takes, by extension.
 *
 * Its own module rather than a seventh export from `icons.tsx`: a file that
 * exports both components and plain functions breaks React Fast Refresh, and
 * the linter says so. A tint rather than eight hand-drawn glyphs, because the
 * extension is already written on the row and eight drawings are eight things
 * to maintain. The classes map to colours in `index.css`.
 */

export function kindOf(name: string): string {
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return "other";
  const ext = name.slice(dot + 1).toLowerCase();
  if (ext === "pdf") return "pdf";
  if (["jpg", "jpeg", "png", "gif", "webp", "svg"].includes(ext)) return "image";
  if (["txt", "md", "rtf", "doc", "docx"].includes(ext)) return "text";
  if (["csv", "xls", "xlsx", "tsv"].includes(ext)) return "sheet";
  if (["zip", "tar", "gz", "7z", "rar"].includes(ext)) return "archive";
  return "other";
}
