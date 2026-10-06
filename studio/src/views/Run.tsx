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
import { RunAnalytics } from "../components/RunAnalytics";
import { WorkerCognitionView } from "../components/WorkerCognitionView";
import { FilePreviewPanel } from "../components/FilePreviewPanel";
import { GitGraph } from "../components/GitGraph";
import { GitDiffSideOver } from "../components/GitDiffSideOver";
import { TaskBoard } from "../features/board";
import { useRunCards } from "../api/queries";
import { createRunStore } from "../stores/runEvents";
import { RunStoreProvider } from "../stores/runStoreContext";
import type { RawSignal } from "../lib/runEvents.gen";
import { PipelineGraph } from "../components/PipelineGraph";
import { useRunGraph } from "../hooks/useRunGraph";
import { useDesks } from "../hooks/useDesks";
import { QuipFeed } from "../components/QuipFeed";
import { ScreenshotPane } from "../components/ScreenshotPane";
import { TagsPanel } from "../components/TagsPanel";
import { PersonaSlideOver } from "../components/PersonaSlideOver";
import { WorkerCard } from "../components/WorkerCard";
import { WorkerSettingsPanel } from "../components/WorkerSettingsPanel";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "../components/ui/dialog";
import { Alert, Label, Page, Textarea, linkClass } from "../components/ui/form-controls";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import { runStatusVariant } from "../lib/field";
import { cn } from "../lib/utils";
import { RunWsProvider } from "../contexts/RunWsContext";
import type { RunWsSubscriber } from "../contexts/RunWsContext";
import { summariseAgents, signalsForRoleIteration } from "../lib/iterations";
import { isTerminalStatus } from "../lib/runStatus";
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

const isTerminal = isTerminalStatus;

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
  const wsRef = useRef<{ close: () => void } | null>(null);
  const signalHandlersRef = useRef<Set<(s: Signal) => void>>(new Set());
  const [slideOverPersonaId, setSlideOverPersonaId] = useState<string | null>(null);
  const [agentDetailRole, setAgentDetailRole] = useState<string | null>(null);
  const [resuming, setResuming] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [boardTab, setBoardTab] = useState<"signals" | "kanban" | "workers" | "git" | "files" | "analytics">("signals");
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
    <Page className="max-w-full">
      <div className="grid h-[calc(100vh-90px)] grid-cols-[minmax(0,2fr)_minmax(0,1fr)] grid-rows-[auto_auto_minmax(0,1fr)] gap-4">
        <div className="col-span-2">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h1 className="m-0 text-[22px] font-semibold tracking-tight">
                Run <span className="font-mono">{runId}</span>
              </h1>
              {meta && (
                <span className="text-xs text-muted-foreground">
                  project <Link className={linkClass} to={`/projects/${meta.project_id}`}>{meta.project_id}</Link>
                  {" — "}
                  <Badge variant={runStatusVariant(meta.status)}>{meta.status}</Badge>
                </span>
              )}
              {!meta && <span className="text-xs text-muted-foreground">live (no on-disk meta yet)</span>}
              {" "}
              <Link to={`/runs/${runId}`} className={cn(linkClass, "text-xs")}>Cockpit view</Link>
            </div>
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <Badge variant={connected ? "running" : "secondary"}>
                {connected ? "● live" : "○ disconnected"}
              </Badge>
              <span>{signals.length} signals</span>
              {/* Stop button — visible while the run is active (no
                  on-disk meta yet, or meta exists but isn't terminal). */}
              {(!meta || !isTerminal(meta.status)) && connected && (
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() => void handleStop()}
                  disabled={stopping}
                  title="Soft-cancel: in-flight agents finish; no new dispatches"
                >
                  {stopping ? "Stopping…" : "Stop run"}
                </Button>
              )}
              {/* Phase G.3 — resume button on failed/non-complete runs.
                  Calls runsApi.resume() (G-prime.2 backend). */}
              {meta && meta.status !== "complete" && isTerminal(meta.status) && (
                <Button
                  size="sm"
                  onClick={() => void handleResume()}
                  disabled={resuming}
                  title="Re-enqueue from the persisted state"
                >
                  {resuming ? "Resuming…" : "Resume run"}
                </Button>
              )}
              {meta && isTerminal(meta.status) && meta.project_id && (
                <Button
                  size="sm"
                  onClick={() => setContinueOpen(true)}
                  title="Start a new run that builds on this one"
                >
                  Continue from this run
                </Button>
              )}
            </div>
          </div>
          {error && <Alert variant="error" className="mt-3">{error}</Alert>}

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
              <Card className="mt-2 border-status-running/40 bg-status-running/10 px-3 py-2">
                <strong>Resumed run</strong>
                <span className="ml-2 text-xs text-muted-foreground">
                  {resumedSig.summary}
                </span>
              </Card>
            );
          })()}

          {/* Track 4.2 — PR-ready banner. Shown when pr_mode=manual and the
              runner has merged leaf work to the integration branch. The user
              opens the PR themselves; we surface the paste-ready gh command. */}
          {(() => {
            if (!meta || !isTerminal(meta.status)) return null;
            let prReady = meta.pr_ready;
            if (!prReady) {
              const sig = [...signals].reverse().find((s) => s.type === "PR_READY");
              if (sig?.detail) {
                try { prReady = JSON.parse(sig.detail); } catch { /* noop */ }
              }
            }
            if (!prReady) return null;
            return (
              <Card className="mt-3 border-l-4 border-l-status-complete p-4">
                <div className="font-semibold">
                  🌿 Branch ready: <span className="font-mono">{prReady.integration_branch}</span>
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  Open the PR yourself with:
                </div>
                <div className="mt-1 flex items-center gap-1.5">
                  <code className="flex-1 overflow-x-auto rounded bg-muted px-2 py-1.5 font-mono text-xs">
                    {prReady.gh_command}
                  </code>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      if (prReady) void navigator.clipboard.writeText(prReady.gh_command);
                    }}
                    title="Copy to clipboard"
                  >
                    Copy
                  </Button>
                </div>
              </Card>
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
          <div className="col-span-2 max-h-[220px] overflow-y-auto border-b border-border bg-muted/40 px-4 py-3">
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

        <div className="col-start-1 row-start-3 flex min-h-0 flex-col overflow-y-auto">
          <Tabs value={boardTab} onValueChange={(v) => setBoardTab(v as typeof boardTab)}>
            <TabsList className="mb-2">
              {(["signals", "kanban", "workers", "git", "files", "analytics"] as const).map((tab) => (
                <TabsTrigger key={tab} value={tab}>
                  {tab === "signals" ? "Board"
                    : tab === "kanban" ? "Kanban"
                    : tab === "workers" ? "Workers"
                    : tab === "git" ? "Git"
                    : tab === "files" ? "Files"
                    : "Analytics"}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          {boardTab === "signals" && <BoardFeed signals={signals} />}
          {boardTab === "analytics" && runId && (
            <RunAnalytics runId={runId} isActive={!isTerminal(meta?.status)} />
          )}
          {boardTab === "workers" && runId && (
            <WorkerCognitionView runId={runId} isActive={!isTerminal(meta?.status)} />
          )}
          {boardTab === "kanban" && runId && (
            <>
              <LegacyTaskBoard runId={runId} signals={signals} />
              <div className="mt-auto border-t border-border pt-2">
                <CommentBox runId={runId} />
              </div>
            </>
          )}
          {boardTab === "git" && runId && (
            <div className="p-3">
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

        <div className="col-start-2 row-start-3 flex min-h-0 flex-col gap-3 overflow-y-auto">
          {project && (project.agents ?? []).length > 0 && (
            <div>
              <div className="mb-1.5 text-xs font-semibold uppercase text-muted-foreground">Workers</div>
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
        <Dialog open onOpenChange={(o) => !o && !continueBusy && setContinueOpen(false)}>
          <DialogContent>
            <DialogTitle>Continue from run {runId}</DialogTitle>
            <DialogDescription>
              The next run on project{" "}
              <span className="font-mono">{meta.project_id}</span> will start with
              this run's deliverables attached. Add feedback below — it lands
              as <span className="font-mono">feedback.md</span> in the next run's
              inputs.
            </DialogDescription>
            <div>
              <Label htmlFor="continue-feedback">What should change?</Label>
              <Textarea
                id="continue-feedback"
                rows={5}
                autoFocus
                placeholder="e.g. Token expiry was not enforced. Add an exp claim and a refresh-token endpoint."
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
              />
            </div>
            <DialogFooter className="justify-start">
              <Button onClick={() => void continueFromRun()} disabled={continueBusy}>
                {continueBusy ? "Setting up…" : "Continue"}
              </Button>
              <Button variant="outline" onClick={() => setContinueOpen(false)} disabled={continueBusy}>
                Cancel
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
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
    </Page>
    </RunWsProvider>
  );
}

/** The task board inside the legacy view: it needs a run store, fed here from the view's own signal list. */
function LegacyTaskBoard({ runId, signals }: { runId: string; signals: Signal[] }) {
  const store = useMemo(() => createRunStore(runId), [runId]);
  const cards = useRunCards(runId);
  useEffect(() => {
    store.getState().ingest(signals as unknown as RawSignal[]);
  }, [store, signals]);
  useEffect(() => {
    if (cards.data) store.getState().seedCards(cards.data);
  }, [store, cards.data]);
  return (
    <RunStoreProvider store={store}>
      <TaskBoard runId={runId} />
    </RunStoreProvider>
  );
}
