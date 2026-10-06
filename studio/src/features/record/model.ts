import type { RecordEventKind, RecordEventWire } from "./api";

const ID = "R[0-9]+(?:\\.[0-9]+)?|D[0-9]+|B[0-9]+";

export interface TextPart {
  text: string;
  /** Set when the part is an id that can be traced again. */
  ref?: string;
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Splits an event's text into plain parts and traceable ids (R3, D4, B7 and the event's links). */
export function linkify(text: string, links: readonly string[] = []): TextPart[] {
  const extra = links.filter((l) => l !== "" && !new RegExp(`^(?:${ID})$`).test(l)).map(escapeRe);
  const re = new RegExp(`\\b(?:${[ID, ...extra].join("|")})\\b`, "g");
  const parts: TextPart[] = [];
  let last = 0;
  for (const m of text.matchAll(re)) {
    const at = m.index ?? 0;
    if (at > last) parts.push({ text: text.slice(last, at) });
    parts.push({ text: m[0], ref: m[0] });
    last = at + m[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last) });
  return parts;
}

export function eventTime(at: number): string {
  return new Date(at * 1000).toISOString().slice(0, 16).replace("T", " ");
}

/** "3 versions" for a requirement or decision; otherwise the number of events before the status line. */
export function versionCount(events: readonly RecordEventWire[]): string {
  const versions = events.filter((e) => e.kind === "spec" || e.kind === "decision").length;
  if (versions > 0) return `${versions} version${versions === 1 ? "" : "s"}`;
  const n = events.filter((e) => e.kind !== "status").length;
  return `${n} event${n === 1 ? "" : "s"}`;
}

export const KIND_LABEL: Record<RecordEventKind, string> = {
  spec: "spec",
  decision: "decision",
  plan: "plan",
  work: "work",
  session: "session",
  review: "review",
  status: "now",
};
