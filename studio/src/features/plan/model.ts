import type {
  ChangeWire, CheckMethod, CheckSpecWire, DraftChangeWire, DraftWire, ParamValue, PlanMessageWire, SpecWire,
} from "./api";

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

/** One piece of a word-level diff. */
export interface DiffPiece {
  text: string;
  kind: "same" | "add" | "del";
}

/** Word diff, or the old and new text as two blocks when most of it was rewritten. */
export function readableDiff(a: string, b: string): DiffPiece[] {
  const d = wordDiff(a, b);
  const kept = d.filter((p) => p.kind === "same").reduce((n, p) => n + p.text.trim().length, 0);
  if (a && kept < 0.4 * Math.max(a.length, b.length)) return [{ text: a, kind: "del" }, { text: b, kind: "add" }];
  return d;
}

/** Word-level diff (LCS over words and the spaces between them). */
export function wordDiff(a: string, b: string): DiffPiece[] {
  const x = a.split(/(\s+)/).filter((t) => t !== "");
  const y = b.split(/(\s+)/).filter((t) => t !== "");
  const n = x.length;
  const m = y.length;
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--) lcs[i][j] = x[i] === y[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const out: DiffPiece[] = [];
  const push = (text: string, kind: DiffPiece["kind"]) => {
    const last = out[out.length - 1];
    if (last && last.kind === kind) last.text += text;
    else out.push({ text, kind });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) {
      push(x[i], "same");
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) push(x[i++], "del");
    else push(y[j++], "add");
  }
  while (i < n) push(x[i++], "del");
  while (j < m) push(y[j++], "add");
  return out;
}

export interface ParamRow {
  key: string;
  old: string | null;
  next: string | null;
  assumed: boolean;
}

export interface CheckRow {
  id: string;
  method: CheckMethod | null;
  /** For edits: the diff of the check text; otherwise one "same"/"add"/"del" piece. */
  text: DiffPiece[];
  test: string | null;
}

function paramText(value: ParamValue | undefined, unit?: string | null): string | null {
  if (value === undefined) return null;
  return `${String(value)}${unit ? ` ${unit}` : ""}`;
}

function checkSpec(c: string | CheckSpecWire): CheckSpecWire {
  return typeof c === "string" ? { text: c } : c;
}

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
  /** Style problems found when the change was proposed (a person decides). */
  warnings: string[];
  /** For role edits: each changed field with its old value. */
  teamFields: TeamFieldChange[];
  /** Theme label set by the project manager, if any. */
  group: string | null;
  /** The requirement a requirement change touches, and its title (for the row label). */
  reqId: string | null;
  reqTitle: string | null;
  /** set_statement / set_title: word diff of the old and new text. */
  textDiff: DiffPiece[] | null;
  /** set_param / remove_param, and the params of a new requirement. */
  params: ParamRow[];
  /** add_check / edit_check / remove_check, and the checks of a new requirement. */
  checks: CheckRow[];
  /** add_item / edit_item: acceptance lines, check references resolved, marked added/removed. */
  itemLines: ItemLine[];
  change: ChangeWire;
}

export interface ItemLine {
  /** The check id for a reference ("R12.1"), else null for the item's own line. */
  ref: string | null;
  text: string;
  kind: "same" | "add" | "del";
}

/** Minimal backlog item shape the draft view needs (features/work/api BacklogItemWire). */
export interface DraftItem {
  id: string;
  title: string;
  acceptance: string[];
}

const CHECK_REF = /^(R\d+\.\d+)(?::\s*(.*))?$/s;

function resolveLine(line: string, spec: SpecWire | undefined): ItemLine {
  const m = line.trim().match(CHECK_REF);
  if (!m) return { ref: null, text: line, kind: "same" };
  const req = spec?.requirements.find((r) => r.id === m[1].split(".")[0]);
  const check = req?.checks.find((k) => k.id === m[1]);
  return { ref: m[1], text: check?.text ?? m[2] ?? "(not in the spec)", kind: "same" };
}

function itemLines(c: ChangeWire, spec: SpecWire | undefined, items: DraftItem[] | undefined): ItemLine[] {
  if (c.op !== "add_item" && !(c.op === "edit_item" && c.acceptance)) return [];
  const next = (c.acceptance ?? []).map((l) => resolveLine(l, spec));
  if (c.op === "add_item") {
    // Checks of the requirements it serves are added by reference on approval.
    const served = (c.serves ?? []).flatMap((rid) => spec?.requirements.find((r) => r.id === rid)?.checks ?? []);
    return [...served.map((k) => ({ ref: k.id, text: k.text, kind: "add" as const })), ...next.map((l) => ({ ...l, kind: "add" as const }))];
  }
  const old = (items?.find((i) => i.id === c.id)?.acceptance ?? []).map((l) => resolveLine(l, spec));
  const key = (l: ItemLine) => l.ref ?? l.text.trim().toLowerCase();
  const oldKeys = new Set(old.map(key));
  const newKeys = new Set(next.map(key));
  return [
    ...next.map((l) => ({ ...l, kind: oldKeys.has(key(l)) ? ("same" as const) : ("add" as const) })),
    ...old.filter((l) => !newKeys.has(key(l))).map((l) => ({ ...l, kind: "del" as const })),
  ];
}

const FIELD_OPS = new Set(["set_title", "set_statement", "set_param", "remove_param", "add_check", "edit_check", "remove_check"]);

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
  if (FIELD_OPS.has(op)) return "Requirements";
  if (op.endsWith("_role") || op === "set_kind" || op === "add_contract_rule") return "Team";
  if (op.endsWith("requirement")) return "Requirements";
  if (op.endsWith("decision")) return "Decisions";
  if (op.endsWith("rule")) return "Rules";
  return "Work items";
}

function markerOf(op: string): Marker {
  if (op === "set_param" || op === "set_title" || op === "set_statement") return "~";
  if (op.startsWith("add_")) return "+";
  if (op.startsWith("drop_") || op.startsWith("remove_")) return "−";
  return "~";
}

/** The label a human sees for a ref such as "new:1" (shown as "new 1"). */
export function refLabel(ref: string): string {
  return ref.replace(/^new:/, "new ");
}

function editedFields(c: ChangeWire): string {
  const skip = new Set(["op", "id", "ref", "group"]);
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
    case "set_title":
      return "Rename";
    case "set_statement":
      return "New statement";
    case "set_param":
      return `Set ${c.key}`;
    case "remove_param":
      return `Remove ${c.key}`;
    case "add_check":
      return "Add a check";
    case "edit_check":
      return "Change a check";
    case "remove_check":
      return "Remove a check";
    case "add_role":
      return `${c.role_type ?? "role"} · ${c.model_tier ?? "solid"}${c.contract ? ` · ${c.contract.replace(/_/g, " ")} contract` : ""}`;
    case "set_kind":
      return `Project kind: ${(c.kind ?? "").replace(/_/g, " ")}${c.start_mode ? `, start mode ${c.start_mode.replace(/_/g, " ")}` : ""}`;
    case "add_contract_rule":
      return `${(c.contract ?? "").replace(/_/g, " ")} rule: ${c.rule ?? ""}${c.evidence_runs?.length ? ` (from ${c.evidence_runs.join(", ")})` : ""}`;
    case "edit_role":
      return "Changes the role";
    case "remove_role":
      return "Remove from the team";
    case "add_requirement":
      return c.title || c.text || "";
    case "edit_requirement":
      return editedFields(c);
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
      return editedFields(c);
    default:
      return c.op;
  }
}

function structured(c: ChangeWire, spec: SpecWire | undefined) {
  const reqId = FIELD_OPS.has(c.op) ? (c.id ?? "").split(".")[0] || null : c.op.endsWith("requirement") ? (c.id ?? null) : null;
  const req = reqId ? spec?.requirements.find((r) => r.id === reqId) : undefined;
  let textDiff: DiffPiece[] | null = null;
  const params: ParamRow[] = [];
  const checks: CheckRow[] = [];
  if ((c.op === "set_statement" || c.op === "edit_requirement") && c.text !== undefined && c.text !== null)
    textDiff = readableDiff(req?.text ?? "", c.text);
  if (c.op === "edit_requirement" && c.checks && req) {
    // An older whole-list edit: match checks by text; the rest are added or removed.
    const next = c.checks.map((k) => checkSpec(k).text.trim());
    const kept = new Set(next.map((t) => t.toLowerCase()));
    for (const old of req.checks)
      if (!kept.has(old.text.trim().toLowerCase()))
        checks.push({ id: old.id, method: old.method ?? null, text: [{ text: old.text, kind: "del" }], test: old.test ?? null });
    const had = new Set(req.checks.map((k) => k.text.trim().toLowerCase()));
    next.forEach((t, i) => {
      if (!had.has(t.toLowerCase())) checks.push({ id: `new ${i + 1}`, method: null, text: [{ text: t, kind: "add" }], test: null });
    });
  }
  if (c.op === "set_title" && c.title !== undefined) textDiff = wordDiff(req?.title ?? "", c.title);
  if (c.op === "set_param" || c.op === "remove_param") {
    const old = req?.params?.find((p) => p.key === c.key);
    params.push({
      key: c.key ?? "",
      old: old ? paramText(old.value, old.unit) : null,
      next: c.op === "remove_param" ? null : paramText(c.value, c.unit ?? old?.unit),
      assumed: c.op === "set_param" && (c.assumed ?? old?.assumed ?? false),
    });
  }
  if (c.op === "add_requirement") {
    for (const p of c.params ?? []) params.push({ key: p.key, old: null, next: paramText(p.value, p.unit), assumed: !!p.assumed });
    (c.checks ?? []).forEach((raw, i) => {
      const k = checkSpec(raw);
      checks.push({ id: `${i + 1}`, method: k.method ?? null, text: [{ text: k.text, kind: "add" }], test: k.test ?? null });
    });
  }
  if (c.op === "add_check" && c.check) {
    checks.push({ id: "new", method: c.check.method ?? null, text: [{ text: c.check.text, kind: "add" }], test: c.check.test ?? null });
  }
  if (c.op === "edit_check" || c.op === "remove_check") {
    const old = req?.checks.find((k) => k.id === c.id);
    const before = old?.text ?? "";
    checks.push({
      id: c.id ?? "",
      method: (c.method ?? old?.method ?? null) as CheckMethod | null,
      text: c.op === "remove_check" ? [{ text: before, kind: "del" }] : c.text !== undefined && c.text !== null ? readableDiff(before, c.text) : [{ text: before, kind: "same" }],
      test: (c.test ?? old?.test ?? null) as string | null,
    });
  }
  return { reqId, reqTitle: req?.title ?? (c.op === "add_requirement" ? (c.title ?? null) : null), textDiff, params, checks };
}

export function viewChange(dc: DraftChangeWire, spec: SpecWire | undefined, team?: TeamMember[], items?: DraftItem[]): ChangeView {
  const c = dc.change;
  const oldItem = c.op === "edit_item" ? items?.find((i) => i.id === c.id) : undefined;
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
    warnings: dc.warnings ?? [],
    teamFields: teamFieldChanges(c, team),
    group: c.group ?? null,
    ...structured(c, spec),
    ...(oldItem && c.title ? { textDiff: wordDiff(oldItem.title, c.title) } : {}),
    itemLines: itemLines(c, spec, items),
    change: c,
  };
}

/** Draft changes by theme (`group`), in first-seen order; ungrouped changes come last as null. */
export function themeGroups(views: ChangeView[]): { group: string | null; changes: ChangeView[] }[] {
  const order: (string | null)[] = [];
  const by = new Map<string | null, ChangeView[]>();
  for (const v of views) {
    if (!by.has(v.group)) {
      by.set(v.group, []);
      order.push(v.group);
    }
    by.get(v.group)!.push(v);
  }
  order.sort((a, b) => (a === null ? 1 : 0) - (b === null ? 1 : 0));
  return order.map((group) => ({ group, changes: by.get(group)! }));
}

/** Draft changes grouped by section, in the order the plan shows them; empty sections are left out. */
export function groupChanges(
  draft: DraftWire,
  spec: SpecWire | undefined,
  team?: TeamMember[],
  items?: DraftItem[],
): { section: Section; changes: ChangeView[] }[] {
  const views = draft.changes.map((c) => viewChange(c, spec, team, items));
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

export interface FoldedGroup {
  key: string;
  label: string;
  messages: PlanMessageWire[];
}

/**
 * Split the conversation into earlier threads (folded) and the open one (plan §11.3).
 * A thread ends where a draft's messages end; the open thread is the current draft's
 * messages and everything after the last earlier draft. Messages without a draft (just
 * discussing) belong to the thread they sit in.
 */
export function foldThread(
  messages: readonly PlanMessageWire[],
  currentDraftId: string | null,
): { folded: FoldedGroup[]; open: PlanMessageWire[] } {
  let cut = 0;
  for (let i = 0; i < messages.length; i++) {
    const d = messages[i].draft_id;
    if (d && d !== currentDraftId) cut = i + 1;
  }
  // Keep at least the last exchange open even when every draft is closed.
  if (!currentDraftId && cut >= messages.length) cut = Math.max(0, messages.length - 2);
  const folded: FoldedGroup[] = [];
  let group: PlanMessageWire[] = [];
  let draft: string | null = null;
  const close = () => {
    if (group.length) {
      const n = draft?.match(/(\d+)$/)?.[1];
      folded.push({ key: `${group[0].id}`, label: n ? `Draft ${n}` : "Earlier conversation", messages: group });
    }
    group = [];
    draft = null;
  };
  for (const m of messages.slice(0, cut)) {
    if (m.draft_id && draft && m.draft_id !== draft) close();
    group.push(m);
    if (m.draft_id) draft = m.draft_id;
  }
  close();
  return { folded, open: messages.slice(cut) };
}
