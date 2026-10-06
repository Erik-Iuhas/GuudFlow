import { useEffect, useState } from "react";
import { runsApi } from "../api";
import type { PriorRunAttachment, RunMeta } from "../types";
import { errorCls, fieldCls, labelCls } from "../lib/field";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

export interface PriorRunPickerProps {
  projectId: string;
  attached: PriorRunAttachment[];
  onAttach: (runId: string, feedback: string) => Promise<void>;
  onDetach: (runId: string) => Promise<void>;
}

export function PriorRunPicker({
  projectId,
  attached,
  onAttach,
  onDetach,
}: PriorRunPickerProps) {
  const [runs, setRuns] = useState<RunMeta[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function load() {
    try {
      const rs = await runsApi.list(projectId);
      setRuns(rs);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const attachedIds = new Set(attached.map((a) => a.run_id));
  const eligible = (runs ?? []).filter((r) => !attachedIds.has(r.run_id));

  async function attach() {
    if (!selectedRun) return;
    setBusy(true);
    try {
      await onAttach(selectedRun, feedback.trim());
      setSelectedRun(null);
      setFeedback("");
      setPicking(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      {attached.length === 0 ? (
        <p className="my-2 text-xs text-muted-foreground">No prior runs attached.</p>
      ) : (
        <ul className="my-2 list-none p-0">
          {attached.map((a) => (
            <li key={a.run_id} className="flex items-center gap-2 border-b border-border py-1.5">
              <span className="font-mono">{a.run_id}</span>
              {a.has_feedback && <Badge variant="secondary">has feedback</Badge>}
              <span className="text-xs text-muted-foreground">
                attached {a.attached_at?.replace("T", " ").split(".")[0]}
              </span>
              <Button size="sm" variant="destructive" onClick={() => void onDetach(a.run_id)}>
                Detach
              </Button>
            </li>
          ))}
        </ul>
      )}

      {picking ? (
        <Card className="mt-2 p-4">
          {error && <div className={errorCls}>{error}</div>}
          {runs === null ? (
            <p className="m-0 text-xs text-muted-foreground">Loading runs…</p>
          ) : eligible.length === 0 ? (
            <p className="m-0 text-xs text-muted-foreground">
              No prior runs available — every completed run is already
              attached, or this project has no prior runs.
            </p>
          ) : (
            <>
              <label className={labelCls}>Pick a run</label>
              <select
                className={fieldCls}
                value={selectedRun ?? ""}
                onChange={(e) => setSelectedRun(e.target.value || null)}
              >
                <option value="">— select —</option>
                {eligible.map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {r.run_id} · {r.status} ·{" "}
                    {r.created_at?.replace("T", " ").split(".")[0]}
                  </option>
                ))}
              </select>

              <label className={`${labelCls} mt-2`}>Feedback (optional)</label>
              <textarea
                className={fieldCls}
                rows={3}
                placeholder="What should the next run do differently?"
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
              />
            </>
          )}

          <div className="mt-2 flex items-center gap-2">
            <Button onClick={() => void attach()} disabled={!selectedRun || busy}>
              {busy ? "Attaching…" : "Attach"}
            </Button>
            <Button variant="outline" onClick={() => setPicking(false)} disabled={busy}>
              Cancel
            </Button>
          </div>
        </Card>
      ) : (
        <Button
          variant="outline"
          className="mt-2"
          onClick={() => {
            setPicking(true);
            void load();
          }}
        >
          + Attach a prior run
        </Button>
      )}
    </div>
  );
}
