import type { AgentConfig, ProjectSeats, StartMode } from "../../types";

export const START_MODES: { value: StartMode; title: string; hint: string }[] = [
  { value: "spec_first", title: "Spec first", hint: "Agree the spec, then build." },
  { value: "prototype_first", title: "Prototype first", hint: "A rough version first, then write the spec from it." },
  { value: "test_first", title: "Test first", hint: "A tester writes checks from the spec before builders start." },
  { value: "research_first", title: "Research first", hint: "Compare approaches and recommend one before building." },
  { value: "maintain", title: "Maintain", hint: "For existing code: the smallest safe change and a regression test per fix." },
];

export const LEAD_HINT =
  "Technical decisions wait for your approval in plan drafts. Otherwise Jack, the project manager, makes them and records why.";
export const REVIEWER_HINT = "You co-sign the code of each item before it is accepted, after the agent reviewer.";

export const DEFAULT_CAP_USD = 5;

/** A project id from free text: lowercase words joined by dashes, at most 40 characters. */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
    .replace(/-+$/g, "");
}

export interface NewProjectChoices {
  projectId: string;
  goal: string;
  lead: boolean;
  reviewer: boolean;
  startMode: StartMode;
  /** Spend cap per session in USD; null for no cap. */
  capUsd: number | null;
}

export function seatsFor(lead: boolean, reviewer: boolean): ProjectSeats {
  return { lead, reviewer: reviewer ? "all" : "off" };
}

/** The project document sent to `POST /api/projects` (the cap sits with the other approval gates). */
export function buildProject(
  c: NewProjectChoices,
  proposal: { agents: AgentConfig[]; rbac?: Record<string, { allowed_scopes: string[] }> },
) {
  return {
    project_id: c.projectId,
    goal: c.goal,
    agents: proposal.agents,
    rbac: proposal.rbac ?? {},
    max_iterations: 5,
    harness: "opencode" as const, // always explicit: a missing field means opencode
    seats: seatsFor(c.lead, c.reviewer),
    start_mode: c.startMode,
    approval_gates: { token_budget_usd: c.capUsd },
  };
}
