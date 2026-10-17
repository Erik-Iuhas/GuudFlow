import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, approvePlan, invokeApi, messageWorker, runsApi, sendPlanBack } from "../../api";
import { Button } from "../../components/ui/button";
import { planApi, type JackActionWire } from "../plan/api";
import { workApi } from "../work/api";

// Jack's buttons (docs/JACK_UNSTUCK_PLAN.md §5). Jack never acts: each button calls an existing
// route when the owner presses it, and shows what came back. Stopping and raising the budget
// ask for a second press in the button itself.

const CONFIRM = new Set(["raise_limits", "stop"]);
type State = { phase: "idle" | "confirm" | "busy" | "done" | "error" | "gate"; note?: string };

function confirmText(a: JackActionWire): string {
  if (a.kind === "stop") return "Stop the run? Press again";
  const bits = [a.budget_usd ? `budget $${a.budget_usd}` : "", a.extend_minutes ? `+${a.extend_minutes} min` : ""].filter(Boolean);
  return `Raise to ${bits.join(" and ")}? Press again`;
}

export function JackActions({ projectId, actions, onApproved }: {
  projectId: string;
  actions: readonly JackActionWire[];
  onApproved?: () => void;
}) {
  if (!actions.length) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5" data-testid="jack-actions">
      {actions.map((a, i) => <ActionButton key={`${a.kind}-${i}`} projectId={projectId} action={a} onApproved={onApproved} />)}
    </div>
  );
}

function ActionButton({ projectId, action: a, onApproved }: { projectId: string; action: JackActionWire; onApproved?: () => void }) {
  const navigate = useNavigate();
  const [state, setState] = useState<State>({ phase: "idle" });

  async function invoke(payload: Record<string, unknown>, confirm: boolean): Promise<string> {
    const res = await invokeApi.invoke(projectId, { ...payload, ...(confirm ? { confirm: true } : {}) });
    if (res.accepted && res.run_id) {
      navigate(`/runs/${res.run_id}`);
      return `Started ${res.run_id}`;
    }
    setState({ phase: "gate", note: ("message" in res && res.message) || "Check the cost first." });
    throw new GateShown();
  }

  async function run(confirmGate = false): Promise<void> {
    if (CONFIRM.has(a.kind) && state.phase !== "confirm") {
      setState({ phase: "confirm" });
      return;
    }
    setState({ phase: "busy" });
    try {
      const note = await perform(confirmGate);
      setState({ phase: "done", note });
    } catch (e) {
      if (e instanceof GateShown) return;
      setState({ phase: "error", note: e instanceof ApiError ? String(e.detail) : e instanceof Error ? e.message : String(e) });
    }
  }

  async function perform(confirmGate: boolean): Promise<string> {
    const run_id = a.run_id ?? "";
    switch (a.kind) {
      case "answer":
        await runsApi.respond(run_id, a.text ?? "");
        return "Answered · the run carries on";
      case "message_worker":
        await messageWorker(run_id, a.role ?? "", a.text ?? "", false);
        return `Sent · ${a.role} reads it at the start of its next turn`;
      case "plan_approve":
        await approvePlan(run_id);
        return "Plan approved · cards start";
      case "plan_send_back":
        await sendPlanBack(run_id, a.text ?? "");
        return "Sent back · the planner plans again";
      case "raise_limits":
        await runsApi.setLimits(run_id, {
          ...(a.budget_usd ? { budget_usd: a.budget_usd } : {}),
          ...(a.extend_minutes ? { extend_minutes: a.extend_minutes } : {}),
        });
        return "Limits raised";
      case "stop":
        await runsApi.stop(run_id);
        return "Stopping · running turns finish first";
      case "continue_run":
        return invoke({ continue_run: run_id, ...(a.text ? { continue_note: a.text } : {}) }, confirmGate);
      case "start_session":
        return invoke({ backlog_items: a.items ?? [] }, confirmGate);
      case "release_item":
        await workApi.patch(projectId, a.item ?? "", { status: "open" });
        return `${a.item} is open again`;
      case "approve_draft":
        await planApi.approve(projectId);
        onApproved?.();
        return "Draft approved";
      case "open": {
        const t = a.target ?? "";
        if ((t === "run" || t === "card") && run_id) navigate(`/runs/${run_id}${a.card ? `?card=${encodeURIComponent(a.card)}` : ""}`);
        else if (t === "settings" || t === "models") navigate("/settings");
        else if (t === "review") navigate(`/projects/${encodeURIComponent(projectId)}?tab=work`);
        else navigate(`/projects/${encodeURIComponent(projectId)}?tab=${encodeURIComponent(t)}`);
        return "Opened";
      }
    }
  }

  const done = state.phase === "done";
  return (
    <span className="inline-flex flex-col gap-0.5">
      <Button size="sm" variant={state.phase === "confirm" ? "destructive" : "outline"} className="h-7 px-2 text-xs"
        disabled={state.phase === "busy" || done}
        onClick={() => void run(state.phase === "gate")}
        data-testid={`jack-action-${a.kind}`}>
        {done ? `✓ ${a.label}` : state.phase === "busy" ? "Working…" : state.phase === "confirm" ? confirmText(a)
          : state.phase === "gate" ? "Start anyway" : a.label}
      </Button>
      {state.note && (
        <span role={state.phase === "error" ? "alert" : "status"}
          className={`text-[11px] ${state.phase === "error" ? "text-destructive" : "text-muted-foreground"}`}>
          {state.note}
        </span>
      )}
    </span>
  );
}

class GateShown extends Error {}
