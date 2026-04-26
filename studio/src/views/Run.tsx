import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  connectRunStream,
  inputsApi,
  projectsApi,
  runsApi,
} from "../api";
import { AgentStateCard } from "../components/AgentStateCard";
import { ApprovalCard } from "../components/ApprovalCard";
import { BoardFeed } from "../components/BoardFeed";
import { CommentBox } from "../components/CommentBox";
import { GitLogTab } from "../components/GitLogTab";
import { PipelineGraph } from "../components/PipelineGraph";
import { QuipFeed } from "../components/QuipFeed";
import { ScreenshotPane } from "../components/ScreenshotPane";
import { summariseAgents } from "../lib/iterations";
import type { ProjectConfig, RunMeta, Signal } from "../types";

/** Unresolved approval gates: APPROVAL_NEEDED signals whose matching
 *  GRANTED / DENIED hasn't landed on the board yet. */
function unresolvedApprovals(signals: Signal[]): Signal[] {
  const resolved = new Set<string>();
  for (const s of signals) {
    if (s.type === "APPROVAL_GRANTED" || s.type === "APPROVAL_DENIED") {
      const m = (s.detail || "").match(/approval_id=([^;]+)/);
      if (m) resolved.add(m[1].trim());
    }
  }
  return signals.filter((s) => {
    if (s.type !== "APPROVAL_NEEDED") return false;
    const m = (s.detail || "").match(/approval_id=([^;]+)/);
    return !!m && !resolved.has(m[1].trim());
  });
}

const TERMINAL_STATUSES = new Set([
  "complete",
  "max_iterations",
  "stuck",
  "deadline_exceeded",
  "errored",
  "failed",
]);

function isTerminal(status: string | undefined): boolean {
  return !!status && TERMINAL_STATUSES.has(status);
}

export function Run() {
  const { runId } = useParams<{ runId: string }>();
  const [meta, setMeta] = useState<RunMeta | null>(null);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [project, setProject] = useState<ProjectConfig | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [continueOpen, setContinueOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [continueBusy, setContinueBusy] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!runId) return;
    void loadMeta();

    const ws = connectRunStream(
      runId,
      (msg) => {
        if (msg.type === "snapshot" && msg.signals) {
          setSignals(msg.signals);
          setConnected(true);
        } else if (msg.type === "signal" && msg.signal) {
          setSignals((prev) => [...prev, msg.signal as Signal]);
        } else if (msg.type === "error") {
          setError("run not found or finished");
        }
      },
      () => setConnected(false),
    );
    wsRef.current = ws;
    return () => {
      ws.close();
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  async function loadMeta() {
    if (!runId) return;
    try {
      const m = await runsApi.get(runId);
      setMeta(m);
      // Phase G.1 — fetch the project so the pipeline graph can
      // render the agent DAG. Failures here are non-fatal; the
      // graph just won't show.
      if (m.project_id) {
        try {
          setProject(await projectsApi.get(m.project_id));
        } catch {
          /* ignore — graph is optional */
        }
      }
    } catch (e) {
      // Run might still be active and not yet flushed to disk; that's fine.
      if (e instanceof ApiError && e.status !== 404) {
        setError(e.detail);
      }
    }
  }

  async function continueFromRun() {
    if (!runId || !meta?.project_id) return;
    setContinueBusy(true);
    try {
      // 1. Attach this run as input on the project (Chunk 3 endpoint).
      await inputsApi.attachPriorRun(
        meta.project_id,
        runId,
        feedback.trim() || undefined,
      );

      // 2. Stamp pending_continuation on the project so the next run's
      // meta-agent step (Phase D) knows this is a continuation.
      const project = await projectsApi.get(meta.project_id);
      const updated = {
        ...project,
        pending_continuation: {
          run_id: runId,
          attached_at: new Date().toISOString(),
        },
      } as unknown as Parameters<typeof projectsApi.update>[1];
      await projectsApi.update(meta.project_id, updated);

      setContinueOpen(false);
      navigate(`/projects/${meta.project_id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setContinueBusy(false);
    }
  }

  if (!runId) return null;

  return (
    <div className="page" style={{ maxWidth: "100%" }}>
      <div className="run-layout">
        <div className="run-header">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <h1 style={{ marginBottom: 0 }}>
                Run <span className="mono">{runId}</span>
              </h1>
              {meta && (
                <span className="muted small">
                  project <Link to={`/projects/${meta.project_id}`}>{meta.project_id}</Link>
                  {" — "}
                  <span className={`tag tag-status-${meta.status}`}>{meta.status}</span>
                </span>
              )}
              {!meta && <span className="muted small">live (no on-disk meta yet)</span>}
            </div>
            <div className="row small muted">
              <span className={connected ? "tag tag-status-running" : "tag"}>
                {connected ? "● live" : "○ disconnected"}
              </span>
              <span>{signals.length} signals</span>
              {meta && isTerminal(meta.status) && meta.project_id && (
                <button
                  className="primary small"
                  onClick={() => setContinueOpen(true)}
                  title="Start a new run that builds on this one"
                >
                  Continue from this run
                </button>
              )}
            </div>
          </div>
          {error && <div className="error">{error}</div>}

          {/* Pending approval gates (Phase C.0.5). Surfaced inline at
              the top of the run view so the user can grant/deny without
              hunting through the board. */}
          {runId && unresolvedApprovals(signals).map((sig) => (
            <ApprovalCard key={sig.id} runId={runId} signal={sig} />
          ))}
        </div>

        {/* Phase G.1 — pipeline graph + per-agent state cards above
            the BoardFeed so the user can answer "what's the team
            doing right now?" without reading the board linearly. */}
        {project && (project.agents ?? []).length > 0 && (
          <div className="run-pipeline">
            <PipelineGraph
              agents={project.agents ?? []}
              liveStates={summariseAgents(
                (project.agents ?? []).map((a) => a.role),
                signals,
              )}
            />
            <div className="agent-state-cards">
              {Array.from(
                summariseAgents(
                  (project.agents ?? []).map((a) => a.role),
                  signals,
                ).values(),
              ).map((s) => (
                <AgentStateCard
                  key={s.role}
                  summary={s}
                  allSignals={signals}
                />
              ))}
            </div>
          </div>
        )}

        <div className="run-board">
          <BoardFeed signals={signals} />
        </div>

        <div className="run-side">
          <QuipFeed runId={runId} />
          <ScreenshotPane signals={signals} />
          <GitLogTab signals={signals} />
        </div>

        <div className="run-comments">
          <CommentBox runId={runId} />
        </div>
      </div>

      {continueOpen && meta?.project_id && (
        <div
          className="modal-backdrop"
          onClick={() => !continueBusy && setContinueOpen(false)}
        >
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Continue from run {runId}</h3>
            <p className="muted small">
              The next run on project{" "}
              <span className="mono">{meta.project_id}</span> will start with
              this run's deliverables attached. Add feedback below — it lands
              as <span className="mono">feedback.md</span> in the next run's
              inputs.
            </p>
            <label>What should change?</label>
            <textarea
              rows={5}
              autoFocus
              placeholder="e.g. Token expiry was not enforced. Add an exp claim and a refresh-token endpoint."
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
            />
            <div className="row" style={{ marginTop: 12, gap: 8 }}>
              <button
                className="primary"
                onClick={() => void continueFromRun()}
                disabled={continueBusy}
              >
                {continueBusy ? "Setting up…" : "Continue"}
              </button>
              <button
                onClick={() => setContinueOpen(false)}
                disabled={continueBusy}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
