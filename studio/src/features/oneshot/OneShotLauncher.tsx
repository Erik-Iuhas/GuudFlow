import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, exampleProjectsApi, invokeApi, oneshotApi } from "../../api";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Switch } from "../../components/ui/switch";
import { formatCost } from "../../lib/cost";
import type { ExampleProjectSummary, OneShotResponse } from "../../types";

export const MAX_BUDGET_USD = 1000;

export interface OneShotFormErrors {
  goal?: string;
  budget?: string;
}

/** Same rules as the backend: a goal, and a budget above 0 and at most 1000 USD. */
export function validateOneShot(goal: string, budget: string): OneShotFormErrors {
  const errors: OneShotFormErrors = {};
  if (!goal.trim()) errors.goal = "Describe what you want done.";
  const n = Number(budget);
  if (budget.trim() === "" || !Number.isFinite(n)) errors.budget = "Enter a budget in USD.";
  else if (n <= 0 || n > MAX_BUDGET_USD)
    errors.budget = `Budget must be more than 0 and at most ${MAX_BUDGET_USD} USD.`;
  return errors;
}

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.detail : String(e);
}

/**
 * The one-shot launcher (plan 10, 12.3): a goal, web research on/off, a budget and an
 * optional demo mode. A 202 either starts the run or asks for approval first.
 */
export function OneShotLauncher() {
  const navigate = useNavigate();
  const [goal, setGoal] = useState("");
  const [web, setWeb] = useState(true);
  const [budget, setBudget] = useState("5");
  const [demo, setDemo] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<OneShotResponse | null>(null);
  const [examples, setExamples] = useState<ExampleProjectSummary[]>([]);

  useEffect(() => {
    let cancelled = false;
    exampleProjectsApi
      .list()
      .then((list) => !cancelled && setExamples(list))
      .catch(() => undefined); // the gallery is optional; the form works without it
    return () => {
      cancelled = true;
    };
  }, []);

  const errors = validateOneShot(goal, budget);
  const invalid = Object.keys(errors).length > 0;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    setError(null);
    if (invalid) return;
    setBusy(true);
    try {
      const res = await oneshotApi.start({
        goal: goal.trim(),
        web: web ? "researcher" : "off",
        budget: Number(budget),
        demo,
      });
      if (res.accepted && res.run_id) navigate(`/runs/${res.run_id}`);
      else if (res.needs_approval) setPending(res);
      else setError("The server did not start the run.");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function approveAndStart() {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      const res = await invokeApi.invoke(pending.project_id, { confirm: true });
      if (res.accepted) navigate(`/runs/${res.run_id}`);
      else setError(res.message);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page flex flex-col gap-4">
      <h1>One-shot</h1>
      <p className="muted small">
        Give one goal. A default team plans, builds and reviews it end to end.
      </p>

      {error && <div className="error" role="alert">{error}</div>}

      {pending ? (
        <Card className="flex flex-col gap-3 p-4" data-testid="needs-approval">
          <h2 className="m-0">The team needs your approval</h2>
          <p className="m-0 text-sm text-muted-foreground">
            {pending.message ?? "Review the team, then start the run."}
          </p>
          {pending.estimated_cost && (
            <Badge variant="outline" className="w-fit">
              ~ {formatCost(pending.estimated_cost.total_usd)} estimated
            </Badge>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="button" disabled={busy} onClick={approveAndStart}>
              {busy ? "Starting…" : "Approve and start"}
            </Button>
            <Button asChild variant="outline">
              <Link to={`/projects/${pending.project_id}`}>Review the team</Link>
            </Button>
          </div>
        </Card>
      ) : (
        <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
          <Card className="flex flex-col gap-3 p-4">
            <label htmlFor="oneshot-goal" className="text-sm font-medium">Goal</label>
            <textarea
              id="oneshot-goal"
              rows={4}
              placeholder="e.g. Build a CLI that converts CSV files to JSON, with tests"
              value={goal}
              aria-invalid={submitted && !!errors.goal}
              onChange={(e) => setGoal(e.target.value)}
            />
            {submitted && errors.goal && (
              <span className="text-xs text-destructive" role="alert">{errors.goal}</span>
            )}

            <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
              <label className="flex items-center gap-2 text-sm">
                <Switch aria-label="Web research" checked={web} onCheckedChange={setWeb} />
                Web research (researcher role)
              </label>
              <label className="flex items-center gap-2 text-sm">
                <Switch aria-label="Demo mode" checked={demo} onCheckedChange={setDemo} />
                Demo mode (auto-approve the team)
              </label>
              <label className="flex items-center gap-2 text-sm" htmlFor="oneshot-budget">
                Budget (USD)
                <input
                  id="oneshot-budget"
                  type="number"
                  min={0}
                  max={MAX_BUDGET_USD}
                  step="any"
                  className="w-24"
                  value={budget}
                  aria-invalid={submitted && !!errors.budget}
                  onChange={(e) => setBudget(e.target.value)}
                />
              </label>
            </div>
            {submitted && errors.budget && (
              <span className="text-xs text-destructive" role="alert">{errors.budget}</span>
            )}

            <div className="flex justify-end">
              <Button type="submit" disabled={busy}>
                {busy ? "Launching…" : "Launch"}
              </Button>
            </div>
          </Card>

          {examples.length > 0 && (
            <section aria-label="Examples">
              <h2>Start from an example</h2>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-3">
                {examples.map((ex) => (
                  <button
                    type="button"
                    key={ex.id}
                    data-testid={`example-${ex.id}`}
                    className="rounded-lg border border-border bg-card p-3 text-left hover:bg-accent"
                    onClick={() => setGoal(ex.default_goal)}
                  >
                    <div className="text-sm font-semibold">{ex.title}</div>
                    <div className="text-xs text-muted-foreground">{ex.summary}</div>
                  </button>
                ))}
              </div>
            </section>
          )}
        </form>
      )}
    </div>
  );
}
