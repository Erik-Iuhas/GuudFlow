import { useEffect, useMemo, useRef, useState } from "react";
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
import { FileTreeTab } from "../components/FileTreeTab";
import { FilePreviewPanel } from "../components/FilePreviewPanel";
import { GitGraph } from "../components/GitGraph";
import { GitDiffSideOver } from "../components/GitDiffSideOver";
import { KanbanBoard } from "../components/KanbanBoard";
import { PipelineGraph } from "../components/PipelineGraph";
import { useRunGraph } from "../hooks/useRunGraph";
import { useDesks } from "../hooks/useDesks";
import { QuipFeed } from "../components/QuipFeed";
import { ScreenshotPane } from "../components/ScreenshotPane";
import { TagsPanel } from "../components/TagsPanel";
import { PersonaSlideOver } from "../components/PersonaSlideOver";
import { WorkerCard } from "../components/WorkerCard";
import { WorkerSettingsPanel } from "../components/WorkerSettingsPanel";
import { RunWsProvider } from "../contexts/RunWsContext";
import type { RunWsSubscriber } from "../contexts/RunWsContext";
import { summariseAgents, signalsForRoleIteration } from "../lib/iterations";
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
  "stopped",
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
  const signalHandlersRef = useRef<Set<(s: Signal) => void>>(new Set());
  const [slideOverPersonaId, setSlideOverPersonaId] = useState<string | null>(null);
  const [agentDetailRole, setAgentDetailRole] = useState<string | null>(null);
  const [resuming, setResuming] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [boardTab, setBoardTab] = useState<"signals" | "kanban" | "git" | "files">("signals");
  const [openFilePath, setOpenFilePath] = useState<string | null>(null);
  const [diffSha, setDiffSha] = useState<string | null>(null);
  const navigate = useNavigate();
  const { data: graphData } = useRunGraph(runId ?? null);
  const { data: desksData } = useDesks(runId ?? null);

  useEffect(() => {
    if (!runId) return;
    void loadMeta();

    // Poll for meta.json until it arrives — the worker may not have written
    // it yet when the browser navigates here immediately after invoke.
    let metaPollTimer: ReturnType<typeof setInterval> | null = setInterval(() => {
      setMeta((prev) => {
        if (prev) {
          // Meta arrived — stop polling.
          if (metaPollTimer) { clearInterval(metaPollTimer); metaPollTimer = null; }
          return prev;
        }
        void loadMeta();
        return prev;
      });
    }, 3000);

    const ws = connectRunStream(
      runId,
      (msg) => {
        if (msg.type === "snapshot" && msg.signals) {
          setSignals(msg.signals);
          setConnected(true);
        } else if (msg.type === "signal" && msg.signal) {
          const sig = msg.signal as Signal;
          setSignals((prev) => [...prev, sig]);
          signalHandlersRef.current.forEach((h) => h(sig));
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
      if (metaPollTimer) clearInterval(metaPollTimer);
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

  async function handleStop() {
    if (!runId) return;
    if (!confirm("Stop this run? Currently-running agents finish their iteration; no new agents will be dispatched. The run can be resumed later.")) {
      return;
    }
    setStopping(true);
    setError(null);
    try {
      await runsApi.stop(runId);
    } catch (e) {
      if (e instanceof ApiError) setError(e.detail);
      else setError(String(e));
    } finally {
      setStopping(false);
    }
  }

  async function handleResume() {
    if (!runId) return;
    setResuming(true);
    setError(null);
    try {
      await runsApi.resume(runId);
      // Close the old WS and open a fresh one — the worker starts a new
      // stream when it picks up the resumed job.
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setConnected(false);
      const ws = connectRunStream(
        runId,
        (msg) => {
          if (msg.type === "snapshot" && msg.signals) {
            setSignals(msg.signals);
            setConnected(true);
          } else if (msg.type === "signal" && msg.signal) {
            const sig = msg.signal as Signal;
            setSignals((prev) => [...prev, sig]);
            signalHandlersRef.current.forEach((h) => h(sig));
          }
        },
        () => setConnected(false),
      );
      wsRef.current = ws;
      await loadMeta();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setResuming(false);
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

  const wsSubscriber = useMemo<RunWsSubscriber>(() => ({
    subscribe: (handler) => {
      signalHandlersRef.current.add(handler);
      return () => signalHandlersRef.current.delete(handler);
    },
  }), []);

  if (!runId) return null;

  return (
    <RunWsProvider value={wsSubscriber}>
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
              {/* Stop button — visible while the run is active (no
                  on-disk meta yet, or meta exists but isn't terminal). */}
              {(!meta || !isTerminal(meta.status)) && connected && (
                <button
                  className="small"
                  style={{ background: "#dc2626", color: "#fff", borderColor: "#dc2626" }}
                  onClick={() => void handleStop()}
                  disabled={stopping}
                  title="Soft-cancel: in-flight agents finish; no new dispatches"
                >
                  {stopping ? "Stopping…" : "Stop run"}
                </button>
              )}
              {/* Phase G.3 — resume button on failed/non-complete runs.
                  Calls runsApi.resume() (G-prime.2 backend). */}
              {meta && meta.status !== "complete" && isTerminal(meta.status) && (
                <button
                  className="primary small"
                  onClick={() => void handleResume()}
                  disabled={resuming}
                  title="Re-enqueue from the persisted state"
                >
                  {resuming ? "Resuming…" : "Resume run"}
                </button>
              )}
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

          {/* Phase G.3 — banner showing this run was resumed from a
              prior point. Reads the synthetic STARTED signal posted
              by the worker's resume branch. */}
          {(() => {
            const resumedSig = signals.find(
              (s) =>
                s.from_agent === "runner" &&
                s.type === "STARTED" &&
                s.summary.startsWith("resumed from iteration"),
            );
            if (!resumedSig) return null;
            return (
              <div className="card resumed-banner">
                <strong>Resumed run</strong>
                <span className="small muted" style={{ marginLeft: 8 }}>
                  {resumedSig.summary}
                </span>
              </div>
            );
          })()}

          {/* Pending approval gates (Phase C.0.5). Surfaced inline at
              the top of the run view so the user can grant/deny without
              hunting through the board. */}
          {runId && unresolvedApprovals(signals).map((sig) => (
            <ApprovalCard key={sig.id} runId={runId} signal={sig} />
          ))}
        </div>

        {/* Pipeline graph — compact, graph only. Agent state cards live
            in the side rail so they don't eat the board's vertical space. */}
        {project && (project.agents ?? []).length > 0 && (
          <div className="run-pipeline">
            <PipelineGraph
              agents={project.agents ?? []}
              liveStates={summariseAgents(
                (project.agents ?? []).map((a) => a.role),
                signals,
              )}
              graphData={graphData ?? undefined}
            />
          </div>
        )}

        <div className="run-board">
          <div className="tabs" style={{ marginBottom: 0, borderBottom: "1px solid #e2e8f0" }}>
            {(["signals", "kanban", "git", "files"] as const).map((tab) => (
              <button
                key={tab}
                className={`tab ${boardTab === tab ? "tab-active" : ""}`}
                onClick={() => setBoardTab(tab)}
              >
                {tab === "signals" ? "Board" : tab === "kanban" ? "Kanban" : tab === "git" ? "Git" : "Files"}
              </button>
            ))}
          </div>
          {boardTab === "signals" && <BoardFeed signals={signals} />}
          {boardTab === "kanban" && runId && (
            <>
              <KanbanBoard
                runId={runId}
                agents={(project?.agents ?? []).map((a) => ({ role: a.role, persona_id: a.persona_id }))}
                onCardClick={(role) => {
                  setBoardTab("signals");
                  // Smooth scroll to role in board feed is handled by BoardFeed itself
                  const el = document.querySelector(`[data-role="${role}"]`);
                  el?.scrollIntoView({ behavior: "smooth" });
                }}
              />
              <div style={{ borderTop: "1px solid #e2e8f0", padding: "8px 0 0", marginTop: "auto" }}>
                <CommentBox runId={runId} />
              </div>
            </>
          )}
          {boardTab === "git" && runId && (
            <div style={{ padding: 12 }}>
              <GitGraph
                runId={runId}
                desksData={desksData}
                onCommitClick={(sha) => setDiffSha(sha)}
              />
            </div>
          )}
          {boardTab === "files" && (
            <FileTreeTab
              signals={signals}
              runId={runId}
              onFileClick={(path) => setOpenFilePath(path)}
            />
          )}
        </div>

        <div className="run-side">
          {project && (project.agents ?? []).length > 0 && (
            <div className="run-side-workers">
              <div className="small" style={{ fontWeight: 600, textTransform: "uppercase", color: "var(--muted)", marginBottom: 6 }}>Workers</div>
              {Array.from(
                summariseAgents(
                  (project.agents ?? []).map((a) => a.role),
                  signals,
                ).values(),
              ).map((s) => {
                const agentCfg = (project.agents ?? []).find((a) => a.role === s.role);
                const graphNode = graphData?.nodes.find((n) => n.role === s.role);
                return (
                  <AgentStateCard
                    key={s.role}
                    summary={s}
                    allSignals={signals}
                    onRetry={s.state === "failed" ? () => handleResume() : undefined}
                    onClick={() => setAgentDetailRole(s.role)}
                    personaId={agentCfg?.persona_id ?? null}
                    personaName={graphNode?.persona_name ?? null}
                  >
                    {agentCfg && (
                      <WorkerCard
                        agent={agentCfg}
                        mode="live"
                        runId={runId}
                        onOpenPersona={(pid) => setSlideOverPersonaId(pid)}
                      />
                    )}
                  </AgentStateCard>
                );
              })}
            </div>
          )}
          <QuipFeed runId={runId} />
          <ScreenshotPane signals={signals} />
          <TagsPanel runId={runId} />
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

      {slideOverPersonaId && (
        <PersonaSlideOver
          personaId={slideOverPersonaId}
          onClose={() => setSlideOverPersonaId(null)}
        />
      )}

      {/* Agent settings slide-over — WorkerSettingsPanel with live hot-reload */}
      {agentDetailRole && project && meta && (() => {
        const agentCfg = (project.agents ?? []).find((a) => a.role === agentDetailRole);
        if (!agentCfg) return null;
        return (
          <WorkerSettingsPanel
            agent={agentCfg}
            allRoles={(project.agents ?? []).map((a) => a.role)}
            projectRbacScopes={(project.rbac ?? {})[agentDetailRole]?.allowed_scopes ?? []}
            onClose={() => setAgentDetailRole(null)}
            onChange={(partial) => {
              setProject((prev) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  agents: (prev.agents ?? []).map((a) =>
                    a.role === agentDetailRole ? { ...a, ...partial } : a,
                  ),
                };
              });
            }}
            onSave={async (partial) => {
              if (!meta.project_id) return;
              // 1. Persist to project config (future runs).
              const latest = {
                ...project,
                agents: (project.agents ?? []).map((a) =>
                  a.role === agentDetailRole ? { ...a, ...partial } : a,
                ),
              };
              await projectsApi.update(meta.project_id, latest);
              // 2. Hot-reload the live run (best-effort — 404 is normal when run is done).
              if (runId) {
                try {
                  await runsApi.patchAgentConfig(runId, agentDetailRole, partial as Record<string, unknown>);
                } catch {
                  // Run not active — project-only update is sufficient.
                }
              }
            }}
            onRbacChange={(add, remove) => {
              if (!meta.project_id) return;
              const rbac = { ...(project.rbac ?? {}) };
              const current = rbac[agentDetailRole]?.allowed_scopes ?? [];
              const next = [...new Set([...current.filter((s) => !remove.includes(s)), ...add])];
              rbac[agentDetailRole] = { allowed_scopes: next };
              const updated = { ...project, rbac };
              setProject(updated);
              void projectsApi.update(meta.project_id, updated);
            }}
          />
        );
      })()}

      {runId && (
        <FilePreviewPanel
          runId={runId}
          filePath={openFilePath}
          onClose={() => setOpenFilePath(null)}
        />
      )}

      {runId && diffSha && (
        <GitDiffSideOver
          runId={runId}
          sha={diffSha}
          onClose={() => setDiffSha(null)}
        />
      )}
    </div>
    </RunWsProvider>
  );
}
