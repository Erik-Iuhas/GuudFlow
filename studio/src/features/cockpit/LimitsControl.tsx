import { useState } from "react";
import { ApiError, runsApi } from "../../api";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/form-controls";

type TimeLimit = "none" | "soft" | "hard";
type Limits = { budget_usd?: number | null; extend_minutes?: number | null; time_limit?: TimeLimit };

const TIME_LIMIT_LABEL: Record<TimeLimit, string> = {
  soft: "Soft: finish running cards",
  hard: "Hard: stop on time",
  none: "No time limit",
};

/** Raise a live run's budget or add time, next to the budget meter. */
export function LimitsControl({ runId, budgetUsd, onSubmit }: {
  runId: string;
  budgetUsd?: number | null;
  /** Tests inject; defaults to runsApi.setLimits. */
  onSubmit?: (limits: Limits) => Promise<unknown>;
}) {
  const [budget, setBudget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function apply(limits: Limits, label: string) {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      await (onSubmit ? onSubmit(limits) : runsApi.setLimits(runId, limits));
      setDone(label);
      setBudget("");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const amount = Number(budget);
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="limits-control">
      <span className="font-medium text-muted-foreground">Limits</span>
      <Input
        aria-label="New budget in USD"
        className="h-8 w-24"
        type="number"
        min={0}
        step="0.5"
        placeholder={budgetUsd != null ? `$ > ${budgetUsd}` : "$ budget"}
        value={budget}
        disabled={busy}
        onChange={(e) => setBudget(e.target.value)}
      />
      <Button size="sm" variant="outline" disabled={busy || !(amount > 0)}
        onClick={() => void apply({ budget_usd: amount }, `Budget set to $${amount}`)}>
        Raise budget
      </Button>
      {[15, 30].map((m) => (
        <Button key={m} size="sm" variant="outline" disabled={busy}
          onClick={() => void apply({ extend_minutes: m }, `Added ${m} minutes`)}>
          +{m} min
        </Button>
      ))}
      <select
        aria-label="Time limit"
        className="h-8 rounded-md border border-input bg-background px-2"
        disabled={busy}
        value=""
        onChange={(e) => {
          const mode = e.target.value as TimeLimit;
          if (mode) void apply({ time_limit: mode }, TIME_LIMIT_LABEL[mode]);
        }}
      >
        <option value="">Time limit…</option>
        {(["soft", "hard", "none"] as const).map((m) => (
          <option key={m} value={m}>
            {TIME_LIMIT_LABEL[m]}
          </option>
        ))}
      </select>
      {done && <span role="status" className="text-muted-foreground">{done}</span>}
      {error && <span role="alert" className="text-destructive">{error}</span>}
    </div>
  );
}
