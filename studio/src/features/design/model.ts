// Pure helpers for the design and phase views (role contracts plan §3, §4.2).

import type { PhaseWire } from "./api";

type DesignChange = { op: string } & Record<string, unknown>;

const str = (v: unknown): string => (typeof v === "string" ? v : "");

/** One line for a design change in a draft: what it adds, changes or drops. */
export function changeLabel(c: DesignChange): string {
  const name = str(c.name) || str(c.title);
  switch (c.op) {
    case "add_component":
      return `New component ${name} owning ${(c.paths as string[] | undefined)?.join(", ") ?? ""}`;
    case "edit_component":
      return `Change component ${str(c.id)}${name ? ` (${name})` : ""}`;
    case "drop_component":
    case "drop_interface":
    case "drop_datatype":
    case "drop_flow":
      return `Drop ${str(c.id)}: ${str(c.reason)}`;
    case "add_interface":
      return `New interface ${str(c.signature) || name}`;
    case "edit_interface":
      return `Change interface ${str(c.id)}${c.signature ? ` to ${str(c.signature)}` : ""}`;
    case "add_datatype":
      return `New data type ${name}`;
    case "edit_datatype":
      return `Change data type ${str(c.id)}`;
    case "add_flow":
      return `New flow: ${name}`;
    case "edit_flow":
      return `Change flow ${str(c.id)}`;
    case "add_tech_decision":
      return `Decide ${name}: ${str(c.choice)}`;
    case "supersede_tech_decision":
      return `Replace decision ${str(c.id)}: ${str(c.choice)}`;
    case "add_debt":
      return `Record debt: ${str(c.text)}`;
    case "resolve_debt":
      return `Resolve debt ${str(c.id)}: ${str(c.how)}`;
    default:
      return c.op.replace(/_/g, " ");
  }
}

/** The label for a backlog item's phase chip, or null when nothing is worth showing. */
export function itemPhaseLabel(phase: string | undefined, phases: PhaseWire[]): string | null {
  if (!phase || phase === "done" || phase === "dropped") return null;
  if (phase === "ready") return null;              // the normal case: no chip
  if (phase === "build") return "in a session";
  if (phase === "accept") return "waiting for your review";
  const p = phases.find((x) => x.id === phase);
  return p ? `waits for ${p.title.toLowerCase()}` : phase;
}
