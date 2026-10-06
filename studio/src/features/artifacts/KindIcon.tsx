import type { ItemKind } from "./model";

const LABELS: Record<ItemKind, string> = {
  image: "Image",
  screenshot: "Screenshot",
  svg: "SVG",
  html: "HTML",
  pdf: "PDF",
  file: "File",
};

const PATHS: Record<ItemKind, string> = {
  image: "M3 5h18v14H3z M3 16l5-5 4 4 3-3 6 6 M15 9.5h.01",
  screenshot: "M3 4h18v12H3z M8 20h8 M12 16v4",
  svg: "M12 3l9 16H3z M12 9v5",
  html: "M8 7l-5 5 5 5 M16 7l5 5-5 5 M14 5l-4 14",
  pdf: "M6 3h8l4 4v14H6z M14 3v4h4 M9 13h6 M9 17h6",
  file: "M6 3h8l4 4v14H6z M14 3v4h4",
};

/** Human label for a kind ("Screenshot"). */
export function kindLabel(kind: ItemKind): string {
  return LABELS[kind];
}

/** Outline icon for a kind. Decorative: the kind is always also written as text. */
export function KindIcon({ kind, className }: { kind: ItemKind; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className ?? "h-5 w-5"}
    >
      <path d={PATHS[kind]} />
    </svg>
  );
}
