/**
 * Inline SVG, not an icon package.
 *
 * Six glyphs do not justify a dependency, a font download or a sprite sheet,
 * and an inline `<svg>` inherits `currentColor` so a row that is hovered,
 * selected or being dragged tints its icon without a second rule. Every one
 * carries `aria-hidden`: the row already has a text label, and a screen
 * reader announcing "folder graphic Documents" is worse than "Documents".
 */

interface Props {
  readonly className?: string;
}

const BOX = { width: 16, height: 16, viewBox: "0 0 16 16", "aria-hidden": true } as const;

export function FolderClosed({ className }: Props) {
  return (
    <svg {...BOX} className={className} fill="none" stroke="currentColor" strokeWidth="1.3">
      <path d="M1.75 4.25a1 1 0 0 1 1-1h3.1a1 1 0 0 1 .7.3l1 1h5.7a1 1 0 0 1 1 1v6.2a1 1 0 0 1-1 1H2.75a1 1 0 0 1-1-1z" />
    </svg>
  );
}

export function FolderOpen({ className }: Props) {
  return (
    <svg {...BOX} className={className} fill="none" stroke="currentColor" strokeWidth="1.3">
      <path d="M1.75 12.75V4.25a1 1 0 0 1 1-1h3.1a1 1 0 0 1 .7.3l1 1h5.7a1 1 0 0 1 1 1v1.2" />
      <path d="M1.75 12.75 3.6 7.4a1 1 0 0 1 .95-.7h9.6a1 1 0 0 1 .95 1.3l-1.5 4.35a1 1 0 0 1-.95.7H2.75a1 1 0 0 1-1-1z" />
    </svg>
  );
}

export function FileGlyph({ className }: Props) {
  return (
    <svg {...BOX} className={className} fill="none" stroke="currentColor" strokeWidth="1.3">
      <path d="M9.25 1.75H4.25a1 1 0 0 0-1 1v10.5a1 1 0 0 0 1 1h7.5a1 1 0 0 0 1-1V5.25z" />
      <path d="M9.25 1.75v2.5a1 1 0 0 0 1 1h2.5" />
    </svg>
  );
}

export function Chevron({ className }: Props) {
  return (
    <svg {...BOX} className={className} fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M6 4l4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function PencilGlyph({ className }: Props) {
  return (
    <svg {...BOX} className={className} fill="none" stroke="currentColor" strokeWidth="1.3">
      <path d="M11.3 2.45a1.2 1.2 0 0 1 1.7 1.7L5.6 11.55l-2.35.65.65-2.35z" />
    </svg>
  );
}

export function BinGlyph({ className }: Props) {
  return (
    <svg {...BOX} className={className} fill="none" stroke="currentColor" strokeWidth="1.3">
      <path d="M2.75 4.25h10.5M6.5 4.25V2.9h3v1.35M4.1 4.25l.6 8.1a1 1 0 0 0 1 .9h4.6a1 1 0 0 0 1-.9l.6-8.1" />
    </svg>
  );
}

export function UpToRootGlyph({ className }: Props) {
  return (
    <svg {...BOX} className={className} fill="none" stroke="currentColor" strokeWidth="1.3">
      <path d="M8 12.5V4.2" strokeLinecap="round" />
      <path d="M4.6 7.4 8 3.9l3.4 3.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M2.75 2.2h10.5" strokeLinecap="round" />
    </svg>
  );
}

export function SunGlyph({ className }: Props) {
  return (
    <svg {...BOX} className={className} fill="none" stroke="currentColor" strokeWidth="1.3">
      <circle cx="8" cy="8" r="3.1" />
      <path
        d="M8 1.4v1.5M8 13.1v1.5M1.4 8h1.5M13.1 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M12.6 3.4l-1 1M4.4 11.6l-1 1"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function MoonGlyph({ className }: Props) {
  return (
    <svg {...BOX} className={className} fill="none" stroke="currentColor" strokeWidth="1.3">
      <path d="M13 9.6A5.6 5.6 0 0 1 6.4 3a5.6 5.6 0 1 0 6.6 6.6z" strokeLinejoin="round" />
    </svg>
  );
}
