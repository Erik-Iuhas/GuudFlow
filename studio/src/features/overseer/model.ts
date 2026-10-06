import type { OverseerInterventionDetail, TypedSignal } from "../../lib/runEvents.gen";

// Pure model for the overseer timeline (plan section 11): one "wake" per overseer
// AGENT_RESULT, with the detectors that fired and the interventions it then made.

export const OVERSEER = "overseer";

export interface WakeIntervention {
  signalId: string;
  timestamp: string;
  data: OverseerInterventionDetail;
  /** True/false once the run ended and the retrospective judged it; null before that. */
  worked: boolean | null;
}

export interface Wake {
  id: string;
  timestamp: string;
  /** Detector findings that woke it (AGENT_RESULT.stalls), in the order they were reported. */
  stalls: string[];
  assessment: string;
  interventions: WakeIntervention[];
}

function assessmentOf(sig: Extract<TypedSignal, { type: "AGENT_RESULT" }>): string {
  const result = sig.data?.result;
  const a = result && typeof result["assessment"] === "string" ? result["assessment"] : "";
  return a || sig.summary.replace(/^overseer:\s*/, "");
}

/**
 * Group the board into wakes. The overseer posts its AGENT_RESULT first, then one
 * OVERSEER_INTERVENTION per intervention, so interventions belong to the latest result.
 * Interventions with no result before them (a cap pause) get a wake of their own.
 */
export function buildWakes(
  signals: readonly TypedSignal[],
  outcomes: ReadonlyMap<string, boolean | null> = new Map(),
): Wake[] {
  const wakes: Wake[] = [];
  for (const sig of signals) {
    if (sig.type === "AGENT_RESULT" && sig.from_agent === OVERSEER) {
      const stalls = (sig.data?.stalls ?? []).filter((s): s is string => typeof s === "string");
      wakes.push({ id: sig.id, timestamp: sig.timestamp, stalls, assessment: assessmentOf(sig), interventions: [] });
    } else if (sig.type === "OVERSEER_INTERVENTION" && sig.data) {
      let wake = wakes[wakes.length - 1];
      if (!wake) {
        wake = { id: `pre-${sig.id}`, timestamp: sig.timestamp, stalls: [], assessment: sig.summary, interventions: [] };
        wakes.push(wake);
      }
      wake.interventions.push({
        signalId: sig.id, timestamp: sig.timestamp, data: sig.data, worked: outcomes.get(sig.id) ?? null,
      });
    }
  }
  return wakes;
}

/** "extend_review" -> "extend review". */
export function interventionLabel(type: string): string {
  return type.replace(/_/g, " ");
}
