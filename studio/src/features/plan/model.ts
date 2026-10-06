import type { ChangeWire, DraftChangeWire, DraftWire, SpecWire } from "./api";

export type Section = "Requirements" | "Decisions" | "Rules" | "Work items";
export const SECTIONS: readonly Section[] = ["Requirements", "Decisions", "Rules", "Work items"];

export type Marker = "+" | "~" | "−";

export interface ChangeView {
  cid: string;
  section: Section;
  marker: Marker;
  /** The id being changed, or the ref of the thing being added. */
  label: string;
  /** The id or ref used in button names ("Accept R8"). */
  name: string;
  summary: string;
  /** For edits: the text the change replaces, looked up in the spec. */
  oldText: string | null;
  accepted: boolean | null;
  byPm: boolean;
  change: ChangeWire;
}

export interface DraftCounts {
  total: number;
  accepted: number;
  rejected: number;
  undecided: number;
}

export function draftCounts(draft: DraftWire): DraftCounts {
  const accepted = draft.changes.filter((c) => c.accepted === true).length;
  const rejected = draft.changes.filter((c) => c.accepted === false).length;
  return { total: draft.changes.length, accepted, rejected, undecided: draft.changes.length - accepted - rejected };
}

function sectionOf(op: string): Section {
  if (op.endsWith("requirement")) return "Requirements";
  if (op.endsWith("decision")) return "Decisions";
  if (op.endsWith("rule")) return "Rules";
  return "Work items";
}

function markerOf(op: string): Marker {
  if (op.startsWith("add_")) return "+";
  if (op.startsWith("drop_")) return "−";
  return "~";
}

/** The label a human sees for a ref such as "new:1" (shown as "new 1"). */
export function refLabel(ref: string): string {
  return ref.replace(/^new:/, "new ");
}

function editedFields(c: ChangeWire): string {
  const skip = new Set(["op", "id", "ref"]);
  const names = Object.entries(c)
    .filter(([k, v]) => !skip.has(k) && v !== undefined && v !== null)
    .map(([k]) => k);
  return names.length ? `Changes ${names.join(", ")}` : "No field set";
}

function oldTextFor(c: ChangeWire, spec: SpecWire | undefined): string | null {
  if (!spec || !c.id) return null;
  if (c.op === "edit_requirement") {
    return c.text === undefined ? null : (spec.requirements.find((x) => x.id === c.id)?.text ?? null);
  }
  if (c.op === "drop_requirement") return spec.requirements.find((x) => x.id === c.id)?.text ?? null;
  if (c.op === "supersede_decision") {
    const d = spec.decisions.find((x) => x.id === c.id);
    return d ? `${d.title}: ${d.choice}` : null;
  }
  if (c.op === "drop_rule") return spec.rules.find((x) => x.id === c.id)?.text ?? null;
  return null;
}

function summaryOf(c: ChangeWire): string {
  switch (c.op) {
    case "add_requirement":
    case "edit_requirement":
      return c.text ?? editedFields(c);
    case "drop_requirement":
    case "drop_rule":
      return c.reason ? `Drop: ${c.reason}` : "Drop";
    case "add_decision":
    case "supersede_decision":
      return `${c.title ?? ""}: ${c.choice ?? ""}`;
    case "add_rule":
      return c.text ?? "";
    case "add_item":
      return c.title ?? "";
    case "edit_item":
      return c.title ?? editedFields(c);
    default:
      return c.op;
  }
}

export function viewChange(dc: DraftChangeWire, spec: SpecWire | undefined): ChangeView {
  const c = dc.change;
  const name = c.id ?? (c.ref ? refLabel(c.ref) : dc.cid);
  return {
    cid: dc.cid,
    section: sectionOf(c.op),
    marker: markerOf(c.op),
    label: c.op === "supersede_decision" && c.ref ? `${c.id} → ${refLabel(c.ref)}` : name,
    name,
    summary: summaryOf(c),
    oldText: oldTextFor(c, spec),
    accepted: dc.accepted,
    byPm: dc.by === "pm",
    change: c,
  };
}

/** Draft changes grouped by section, in the order the plan shows them; empty sections are left out. */
export function groupChanges(
  draft: DraftWire,
  spec: SpecWire | undefined,
): { section: Section; changes: ChangeView[] }[] {
  const views = draft.changes.map((c) => viewChange(c, spec));
  return SECTIONS.map((section) => ({ section, changes: views.filter((v) => v.section === section) })).filter(
    (g) => g.changes.length > 0,
  );
}

/** Tooltip for a disabled Approve button; empty when approval is possible. */
export function approveBlocker(counts: DraftCounts): string {
  return counts.undecided > 0
    ? `${counts.undecided} change${counts.undecided === 1 ? " is" : "s are"} undecided: accept or reject each one first`
    : "";
}

export function timeLabel(at: number): string {
  return new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
