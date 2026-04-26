import { useEffect, useState } from "react";
import { runsApi } from "../api";
import type { PriorRunAttachment, RunMeta } from "../types";

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
        <p className="muted small">No prior runs attached.</p>
      ) : (
        <ul className="prior-runs-list">
          {attached.map((a) => (
            <li key={a.run_id} className="row" style={{ gap: 8 }}>
              <span className="mono">{a.run_id}</span>
              {a.has_feedback && <span className="tag">has feedback</span>}
              <span className="muted small">
                attached {a.attached_at?.replace("T", " ").split(".")[0]}
              </span>
              <button
                className="danger small"
                onClick={() => void onDetach(a.run_id)}
              >
                Detach
              </button>
            </li>
          ))}
        </ul>
      )}

      {picking ? (
        <div className="card" style={{ marginTop: 8 }}>
          {error && <div className="error">{error}</div>}
          {runs === null ? (
            <p className="muted small">Loading runs…</p>
          ) : eligible.length === 0 ? (
            <p className="muted small">
              No prior runs available — every completed run is already
              attached, or this project has no prior runs.
            </p>
          ) : (
            <>
              <label>Pick a run</label>
              <select
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

              <label style={{ marginTop: 8 }}>Feedback (optional)</label>
              <textarea
                rows={3}
                placeholder="What should the next run do differently?"
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
              />
            </>
          )}

          <div className="row" style={{ marginTop: 8, gap: 8 }}>
            <button
              className="primary"
              onClick={() => void attach()}
              disabled={!selectedRun || busy}
            >
              {busy ? "Attaching…" : "Attach"}
            </button>
            <button onClick={() => setPicking(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          style={{ marginTop: 8 }}
          onClick={() => {
            setPicking(true);
            void load();
          }}
        >
          + Attach a prior run
        </button>
      )}
    </div>
  );
}
