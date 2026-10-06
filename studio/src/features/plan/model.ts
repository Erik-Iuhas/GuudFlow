import type { ChangeWire, DraftChangeWire, DraftWire, SpecWire } from "./api";

export type Section = "Requirements" | "Decisions" | "Rules" | "Work items" | "Team";
export const SECTIONS: readonly Section[] = ["Requirements", "Decisions", "Rules", "Work items", "Team"];

/** The role fields a team change can set, in the order they are listed. */
const TEAM_FIELDS = ["role_type", "model_tier", "description", "depends_on", "review_of", "max_review_rounds", "name"] as const;
const TEAM_FIELD_LABEL: Record<string, string> = {
  role_type: "type", model_tier: "tier", description: "description", depends_on: "after", review_of: "reviews",
  max_review_rounds: "review rounds", name: "name",
};

/** The current team (the project's agents); used to show the old values of a role edit. */
export interface TeamMember {
  role: string;
  role_type?: string;
  model_tier?: string;
  description?: string;
  depends_on?: string[];
  review_of?: string[];
  max_review_rounds?: number;
  name?: string | null;
}

export interface TeamFieldChange {
  field: string;
  old: string | null;
  next: string;
}

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
  /** For role edits: each changed field with its old value. */
  teamFields: TeamFieldChange[];
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
  if (op.endsWith("_role")) return "Team";
  if (op.endsWith("requirement")) return "Requirements";
  if (op.endsWith("decision")) return "Decisions";
  if (op.endsWith("rule")) return "Rules";
  return "Work items";
}

function markerOf(op: string): Marker {
  if (op.startsWith("add_")) return "+";
  if (op.startsWith("drop_") || op.startsWith("remove_")) return "−";
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

function teamValue(v: unknown): string {
  if (Array.isArray(v)) return v.length ? v.join(", ") : "none";
  return v === undefined || v === null || v === "" ? "none" : String(v);
}

function teamFieldChanges(c: ChangeWire, team: TeamMember[] | undefined): TeamFieldChange[] {
  if (c.op !== "edit_role") return [];
  const current = team?.find((m) => m.role === c.role) as Record<string, unknown> | undefined;
  const bag = c as unknown as Record<string, unknown>;
  return TEAM_FIELDS.filter((f) => bag[f] !== undefined && bag[f] !== null).map((f) => ({
    field: TEAM_FIELD_LABEL[f],
    old: current ? teamValue(current[f]) : null,
    next: teamValue(bag[f]),
  }));
}

function summaryOf(c: ChangeWire): string {
  switch (c.op) {
    case "add_role":
      return `${c.role_type ?? "role"} · ${c.model_tier ?? "solid"}`;
    case "edit_role":
      return "Changes the role";
    case "remove_role":
      return "Remove from the team";
    case "add_requirement":
    case "edit_requirement":
      return c.text ?? editedFields(c);
    case "drop_requirement":
    case "drop_rule":
    case "drop_item":
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

export function viewChange(dc: DraftChangeWire, spec: SpecWire | undefined, team?: TeamMember[]): ChangeView {
  const c = dc.change;
  const name = c.id ?? c.role ?? (c.ref ? refLabel(c.ref) : dc.cid);
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
    teamFields: teamFieldChanges(c, team),
    change: c,
  };
}

/** Draft changes grouped by section, in the order the plan shows them; empty sections are left out. */
export function groupChanges(
  draft: DraftWire,
  spec: SpecWire | undefined,
  team?: TeamMember[],
): { section: Section; changes: ChangeView[] }[] {
  const views = draft.changes.map((c) => viewChange(c, spec, team));
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
