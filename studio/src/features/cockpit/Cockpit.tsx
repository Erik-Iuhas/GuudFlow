import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useStore } from "zustand";
import { ApiError, runsApi } from "../../api";
import { queryKeys, useProject, useRunMeta } from "../../api/queries";
import { CommentBox } from "../../components/CommentBox";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "../../components/ui/sheet";
import { Segmented } from "../../components/ui/segmented";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { TooltipProvider } from "../../components/ui/tooltip";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { useRunLive } from "../../hooks/useRunLive";
import type { RunSocketState } from "../../hooks/useRunSocket";
import { isTerminalStatus, statusVariant } from "../../lib/runStatus";
import { cn } from "../../lib/utils";
import type { RunStore } from "../../stores/runEvents";
import { RunStoreProvider, useRunStore } from "../../stores/runStoreContext";
import type { ProjectConfig, RunMeta } from "../../types";
import { AgentDetail } from "./AgentDetail";
import { ApprovalsBar } from "./ApprovalsBar";
import { BudgetMeter } from "./BudgetMeter";
import { HumanAnswerBox } from "./HumanAnswerBox";
import { StoppedBar } from "./StoppedBar";
import { PlanCheckpoint } from "./PlanCheckpoint";
import { Deliveries } from "./Deliveries";
import { WakeOverseer } from "../overseer/WakeOverseer";
import { LimitsControl } from "./LimitsControl";
import { ArtifactGallery } from "../artifacts/ArtifactGallery";
import { TaskBoard } from "../board";
import { ConsolePanel } from "./ConsolePanel";
import { OverseerBar } from "./OverseerBar";
import { formatElapsed, mergeRoles, parseTimestamp, roleSpecs } from "./model";
import { OverseerPanel } from "./OverseerPanel";
import { PipelineGraph } from "./PipelineGraph";
import { TeamView } from "./TeamView";
import { useNodeView, type NodeView } from "./useNodeView";
import { CodeBrowser } from "../code/CodeBrowser";
import { OPEN_CODE_EVENT, type CodeSource, type OpenCodeDetail } from "../code/api";
import { lastEditId } from "./RoleChanges";
import { VirtualBoardFeed, VirtualQuipFeed, useQuipStream, type QuipStream } from "./VirtualFeeds";

const DESKTOP_QUERY = "(min-width: 1024px)";

const NODE_VIEW_OPTIONS: { value: NodeView; label: string }[] = [
  { value: "team", label: "Team" },
  { value: "graph", label: "Graph" },
];

type PanelTab = "board" | "quips" | "overseer" | "cards" | "code" | "artifacts" | "console";

// ── Data wrapper ─────────────────────────────────────────────────────────────

/** The run cockpit for one run: opens the run socket, loads meta and project, renders the view. */
export function Cockpit({ runId }: { runId: string }) {
  const live = useRunLive(runId);
  const { store, status, error, retry } = live;
  const isLive = useStore(store, (s) => s.live);
  const meta = useRunMeta(runId, { pollMs: isLive ? 5000 : false });
  const project = useProject(meta.data?.project_id);

  // The run just finished: take one last look at its meta (status, PR info).
  const qc = useQueryClient();
  const wasLive = useRef(isLive);
  useEffect(() => {
    if (wasLive.current && !isLive) void qc.invalidateQueries({ queryKey: queryKeys.run(runId) });
    wasLive.current = isLive;
  }, [isLive, qc, runId]);

  const budgetUsd = meta.data?.token_budget_usd ?? project.data?.approval_gates?.token_budget_usd ?? null;
  return (
    <CockpitView
      runId={runId}
      store={store}
      socket={{ status, error, retry }}
      meta={meta.data}
      project={project.data}
      budgetUsd={budgetUsd}
      onMetaChanged={() => void meta.refetch()}
    />
  );
}

// ── View ─────────────────────────────────────────────────────────────────────

export interface CockpitViewProps {
  runId: string;
  store: RunStore;
  socket: RunSocketState;
  meta?: RunMeta;
  project?: ProjectConfig;
  budgetUsd?: number | null;
  onMetaChanged?: () => void;
}

/** The cockpit layout (docs/STUDIO_UI_SPEC.md section 4), driven by a run store. */
export function CockpitView(props: CockpitViewProps) {
  return (
    <RunStoreProvider store={props.store}>
      <TooltipProvider delayDuration={200}>
        <CockpitLayout {...props} />
      </TooltipProvider>
    </RunStoreProvider>
  );
}

function useElapsed(startMs: number | null, endMs: number | null, ticking: boolean): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [ticking]);
  if (startMs === null) return null;
  const end = ticking ? now : (endMs ?? startMs);
  return Math.max(0, (end - startMs) / 1000);
}

function earliest(a: number | null, b: number | null): number | null {
  if (a === null) return b;
  if (b === null) return a;
  return Math.min(a, b);
}

function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName);
}

function CockpitLayout({ runId, store, socket, meta, project, budgetUsd, onMetaChanged }: CockpitViewProps) {
  const desktop = useMediaQuery(DESKTOP_QUERY);
  const agents = useRunStore((s) => s.agents);
  const live = useRunStore((s) => s.live);
  const signalCount = useRunStore((s) => s.signals.length);
  const firstTs = useRunStore((s) => s.signals[0]?.timestamp);
  const lastTs = useRunStore((s) => s.signals[s.signals.length - 1]?.timestamp);
  const humanNeeded = useRunStore((s) => s.humanNeeded);
  const stoppedAt = useRunStore((s) => s.stoppedAt);
  // plan §11.4: the latest budget warning, unless the limits changed after it.
  const budgetWarning = useRunStore((s) => {
    for (let i = s.signals.length - 1; i >= 0; i--) {
      const t = s.signals[i].type;
      if (t === "RUN_LIMITS_CHANGED") return null;
      if (t === "BUDGET_WARNING") return s.signals[i].summary;
    }
    return null;
  });
  const interventionCount = useRunStore((s) => s.interventions.length);
  const cardCount = useRunStore((s) => Object.keys(s.cards).length);
  const quips = useQuipStream(runId);
  const names = useRunStore((s) => s.names);

  const [tab, setTab] = useState<PanelTab>("board");
  const [codeFocus, setCodeFocus] = useState<OpenCodeDetail | null>(null);
  useEffect(() => {
    const onOpen = (ev: Event) => {
      const d = (ev as CustomEvent<OpenCodeDetail>).detail;
      if (!d) return;
      setCodeFocus({ ...d });
      setTab("code");
      setPanelOpen(true);
    };
    window.addEventListener(OPEN_CODE_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_CODE_EVENT, onOpen);
  }, []);
  const [graphOpen, setGraphOpen] = useState(true);
  const [nodeView, setNodeView] = useNodeView();
  const [panelOpen, setPanelOpen] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [detailRole, setDetailRole] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"stop" | "resume" | null>(null);

  const graphRef = useRef<HTMLDivElement>(null);
  const commentRef = useRef<HTMLInputElement>(null);
  const focusGraphAfterRender = useRef(false);

  const specs = useMemo(() => roleSpecs(project?.agents), [project]);
  const allSpecs = useMemo(() => mergeRoles(specs, Object.keys(agents)), [specs, agents]);
  const models = useMemo(() => {
    const out: Record<string, string> = {};
    for (const a of project?.agents ?? []) {
      const m = a.model_id || a.model_tier;
      if (m) out[a.role] = m;
    }
    return out;
  }, [project]);
  const anyStarted = useMemo(() => Object.values(agents).some((a) => a.spawns > 0), [agents]);

  const status = meta?.status ?? (live ? "running" : "finished");
  const terminal = isTerminalStatus(meta?.status);
  // The earliest of the two: a finished run's meta is written when it is saved (at the end).
  const startMs = earliest(parseTimestamp(meta?.created_at), parseTimestamp(firstTs));
  const elapsed = useElapsed(startMs, parseTimestamp(lastTs), live && !terminal);

  useEffect(() => {
    if (focusGraphAfterRender.current && graphRef.current) {
      focusGraphAfterRender.current = false;
      graphRef.current.focus();
    }
  }, [nodeView, graphOpen]);

  // HUMAN_NEEDED: pull the cursor to the comment box so the answer is one keystroke away.
  const humanId = humanNeeded?.signalId;
  useEffect(() => {
    if (humanId) commentRef.current?.focus();
  }, [humanId]);

  // Keyboard: g graph, c comment box, a first approval.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey || isTypingTarget(e.target)) return;
      if (e.key === "g") {
        e.preventDefault();
        setNodeView("graph");
        setGraphOpen(true);
        // The graph may not be on screen yet: focus it once it is.
        if (graphRef.current) graphRef.current.focus();
        else focusGraphAfterRender.current = true;
      } else if (e.key === "c") {
        e.preventDefault();
        commentRef.current?.focus();
      } else if (e.key === "a") {
        const first = document.querySelector<HTMLElement>("[data-approvals] button");
        if (first) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setNodeView]);

  // Clicking a node or an agent card opens that role's tool calls and full result.
  const selectRole = useCallback((role: string) => {
    setSelectedRole(role);
    setDetailRole(role);
  }, []);

  async function stop() {
    if (!window.confirm("Stop this run? Running agents finish their turn; no new agents are started. You can resume it later.")) return;
    setBusy("stop");
    setActionError(null);
    try {
      await runsApi.stop(runId);
    } catch (e) {
      setActionError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function resume() {
    setBusy("resume");
    setActionError(null);
    try {
      await runsApi.resume(runId);
      socket.retry();
      onMetaChanged?.();
    } catch (e) {
      setActionError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  const connection =
    socket.status === "open" ? (live ? "live" : "finished") : socket.status === "error" ? "error" : socket.status;

  // Workers a comment can reach (the overseer is told by the run, not by comments).
  const workerRoles = useMemo(() => allSpecs.map((r) => r.role).filter((r) => !r.includes("overseer")), [allSpecs]);
  const overseerPresent = [...allSpecs.map((r) => r.role), ...Object.keys(names)].some((r) => r.includes("overseer"));
  const overseerActive = !stoppedAt && !["complete", "failed", "stuck", "stopped", "max_iterations"].includes(String(status));
  const sidePanel = (
    <SidePanel
      runId={runId}
      tab={tab}
      onTab={setTab}
      quips={quips}
      interventionCount={interventionCount}
      cardCount={cardCount}
      codeFocus={codeFocus}
      projectId={meta?.project_id}
      overseerPresent={overseerPresent}
      overseerActive={overseerActive}
    />
  );

  return (
    <div className="flex flex-col gap-2 text-foreground" data-testid="cockpit">
      {/* Header */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border bg-card px-3 py-2">
        <h1 className="m-0 text-base font-semibold">
          Run <span className="font-mono">{runId}</span>
        </h1>
        {meta?.project_id ? (
          <Link to={`/projects/${meta.project_id}`} className="text-sm text-primary">
            {meta.project_id}
          </Link>
        ) : (
          <span className="text-xs text-muted-foreground">live, no on-disk meta yet</span>
        )}
        <Badge variant={statusVariant(status)} data-testid="run-status">
          {status}
        </Badge>
        <Badge variant={connection === "live" ? "running" : connection === "error" ? "failed" : "outline"} data-testid="connection">
          {connection === "live" ? "● live" : connection}
        </Badge>
        <span className="text-xs text-muted-foreground" data-testid="elapsed">
          {elapsed === null ? "--:--" : formatElapsed(elapsed)} · {signalCount} signals
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Segmented label="Node view" value={nodeView} onChange={setNodeView} options={NODE_VIEW_OPTIONS} />
          <Button size="sm" variant={tab === "code" ? "secondary" : "outline"} aria-pressed={tab === "code"}
                  onClick={() => {
                    setTab("code");
                    if (desktop) setPanelOpen(true);
                    else setDrawerOpen(true);
                  }}>
            Code
          </Button>
          {!desktop && (
            <Button size="sm" variant="outline" onClick={() => setDrawerOpen(true)}>
              Board and feeds
            </Button>
          )}
          {desktop && (
            <>
              {nodeView === "graph" && (
                <Button size="sm" variant="ghost" onClick={() => setGraphOpen((v) => !v)} aria-expanded={graphOpen}>
                  {graphOpen ? "Hide graph" : "Show graph"}
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => setPanelOpen((v) => !v)} aria-expanded={panelOpen}>
                {panelOpen ? "Hide panel" : "Show panel"}
              </Button>
            </>
          )}
          {terminal && (
            <Button size="sm" variant="outline" asChild>
              <Link to={`/runs/${encodeURIComponent(runId)}/report`}>Run report</Link>
            </Button>
          )}
          {!terminal && live && socket.status === "open" && (
            <Button size="sm" variant="destructive" onClick={() => void stop()} disabled={busy !== null}>
              {busy === "stop" ? "Stopping…" : "Stop run"}
            </Button>
          )}
          {meta && terminal && meta.status !== "complete" && (
            <Button size="sm" onClick={() => void resume()} disabled={busy !== null}>
              {busy === "resume" ? "Resuming…" : "Resume run"}
            </Button>
          )}
        </div>
      </header>

      {/* Banners */}
      {(socket.error || socket.status === "error") && (
        <div role="alert" className="flex items-center gap-3 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive" data-testid="socket-error">
          <span>Live feed error: {socket.error ?? "the connection failed"}.</span>
          <Button size="sm" variant="outline" onClick={socket.retry}>
            Retry
          </Button>
        </div>
      )}
      {socket.status === "reconnecting" && (
        <div role="status" className="rounded-md border border-status-blocked/30 bg-status-blocked/10 px-3 py-1.5 text-sm text-status-blocked">
          Reconnecting to the live feed…
        </div>
      )}
      {actionError && (
        <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-1.5 text-sm text-destructive">
          {actionError}
        </div>
      )}
      {humanNeeded && !stoppedAt && humanNeeded.kind === "plan_review" && (
        <PlanCheckpoint runId={runId} role={humanNeeded.role} cards={humanNeeded.cards ?? []}
          onEdit={() => setTab("cards")} />
      )}
      {humanNeeded && !stoppedAt && humanNeeded.kind !== "plan_review" && (
        <div role="alert" className="rounded-md border border-status-waiting/40 bg-status-waiting/10 px-3 py-2 text-sm" data-testid="human-needed">
          <strong>{humanNeeded.role ?? "An agent"} needs you:</strong> {humanNeeded.summary}{" "}
          <HumanAnswerBox runId={runId} />
        </div>
      )}

      {/* Pipeline: the graph has the full width; each node carries its agent's state,
          current card, last result and cost, and opens its tool calls on click. */}
      {!anyStarted && (
        <div className="rounded-md border border-dashed border-border px-3 py-1.5 text-sm text-muted-foreground" data-testid="waiting">
          {socket.status === "error" ? "The live feed is not connected." : "Waiting for the first agent… If this stays empty, the Console tab shows why."}
        </div>
      )}
      {nodeView === "team" && (
        <TeamView specs={allSpecs} models={models} selectedRole={selectedRole} onSelectRole={selectRole} runId={runId} />
      )}
      {nodeView === "graph" && graphOpen && (
        <div
          ref={graphRef}
          tabIndex={0}
          aria-label="Pipeline graph"
          className={cn(
            "w-full shrink-0 overflow-hidden rounded-lg border border-border bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <PipelineGraph specs={allSpecs} selectedRole={selectedRole} onSelectRole={selectRole} direction="LR" fitHeight />
        </div>
      )}
      <OverseerBar runId={runId} present={overseerPresent} active={overseerActive} />

      {desktop && panelOpen && (
        <aside className={cn("flex flex-col overflow-hidden rounded-lg border border-border bg-card",
                              tab === "code" ? "h-[80vh] min-h-[30rem]" : "h-[30rem]")} aria-label="Run panels">
          {sidePanel}
        </aside>
      )}

      {!desktop && (
        <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
          <SheetContent side="right" className="w-full max-w-md gap-0 p-0">
            <SheetTitle className="sr-only">Board and feeds</SheetTitle>
            <SheetDescription className="sr-only">Board, quips, overseer and cards for this run.</SheetDescription>
            <div className="flex min-h-0 flex-1 flex-col pt-8">{sidePanel}</div>
          </SheetContent>
        </Sheet>
      )}

      <Sheet
        open={detailRole !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDetailRole(null);
            setSelectedRole(null);
          }
        }}
      >
        <SheetContent
          side="right"
          expandable
          className="flex w-full max-w-3xl flex-col gap-0 p-0"
          // Focus the sheet itself, not its first control (that pops the capability tooltip).
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            (e.currentTarget as HTMLElement).focus();
          }}
        >
          <SheetTitle className="px-4 pb-1 pt-4 text-base">
            {detailRole && names[detailRole] ? `${names[detailRole]} · ${detailRole}` : detailRole}
          </SheetTitle>
          <SheetDescription className="px-4 pb-2 text-xs">Tool calls and the last result for this role.</SheetDescription>
          {detailRole && <AgentDetail role={detailRole} />}
        </SheetContent>
      </Sheet>

      {/* Bottom bar */}
      <footer className="flex flex-col gap-2 rounded-lg border border-border bg-card px-3 py-2" data-testid="bottom-bar">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <ApprovalsBar runId={runId} />
          </div>
          <div className="flex flex-col items-end gap-1">
            {budgetWarning && live && !terminal && (
              <p role="alert" className="m-0 max-w-md rounded-md border border-status-waiting/50 bg-status-waiting/10 px-2 py-1 text-xs" data-testid="budget-warning">
                {budgetWarning}
              </p>
            )}
            <BudgetMeter budgetUsd={budgetUsd} />
            {live && !terminal && <LimitsControl runId={runId} budgetUsd={budgetUsd} />}
          </div>
        </div>
        {live && !terminal && !stoppedAt ? (
          <div data-testid="comment-bar">
            <CommentBox runId={runId} inputRef={commentRef} roles={workerRoles} names={names} />
            <Deliveries workers={workerRoles} />
          </div>
        ) : (
          <StoppedBar runId={runId} projectId={meta?.project_id} finished={meta?.status === "complete"} />
        )}
        <p className="m-0 text-xs text-muted-foreground">
          Shortcuts: <kbd>g</kbd> graph · <kbd>c</kbd> comment · <kbd>a</kbd> approvals
        </p>
      </footer>
    </div>
  );
}

function SidePanel({
  runId, tab, onTab, quips, interventionCount, cardCount, codeFocus, projectId,
  overseerPresent, overseerActive,
}: {
  runId: string;
  tab: PanelTab;
  onTab: (t: PanelTab) => void;
  quips: QuipStream;
  interventionCount: number;
  cardCount: number;
  codeFocus: OpenCodeDetail | null;
  projectId?: string;
  overseerPresent: boolean;
  overseerActive: boolean;
}) {
  return (
    <Tabs value={tab} onValueChange={(v) => onTab(v as PanelTab)} className="flex min-h-0 flex-1 flex-col">
      <TabsList className="m-2 shrink-0 justify-start overflow-x-auto">
        <TabsTrigger value="board">Board</TabsTrigger>
        <TabsTrigger value="quips">Quips</TabsTrigger>
        <TabsTrigger value="overseer">Overseer{interventionCount > 0 ? ` (${interventionCount})` : ""}</TabsTrigger>
        <TabsTrigger value="cards">Cards{cardCount > 0 ? ` (${cardCount})` : ""}</TabsTrigger>
        <TabsTrigger value="code">Code</TabsTrigger>
        <TabsTrigger value="artifacts">Artifacts</TabsTrigger>
        <TabsTrigger value="console">Console</TabsTrigger>
      </TabsList>
      <TabsContent value="board" className="mt-0 min-h-0 flex-1 data-[state=inactive]:hidden">
        <VirtualBoardFeed />
      </TabsContent>
      <TabsContent value="quips" className="mt-0 min-h-0 flex-1 data-[state=inactive]:hidden">
        <VirtualQuipFeed stream={quips} />
      </TabsContent>
      <TabsContent value="overseer" className="mt-0 min-h-0 flex-1 overflow-y-auto data-[state=inactive]:hidden">
        <WakeOverseer runId={runId} present={overseerPresent} active={overseerActive} />
        <OverseerPanel />
      </TabsContent>
      <TabsContent value="cards" className="mt-0 min-h-0 flex-1 overflow-y-auto data-[state=inactive]:hidden">
        <TaskBoard runId={runId} />
      </TabsContent>
      <TabsContent value="code" className="mt-0 flex min-h-0 flex-1 flex-col p-2 data-[state=inactive]:hidden">
        {tab === "code" && <RunCode runId={runId} projectId={projectId} focus={codeFocus} />}
      </TabsContent>
      <TabsContent value="artifacts" className="mt-0 min-h-0 flex-1 overflow-y-auto data-[state=inactive]:hidden">
        <ArtifactGallery runId={runId} />
      </TabsContent>
      <TabsContent value="console" className="mt-0 min-h-0 flex-1 data-[state=inactive]:hidden">
        {tab === "console" && <ConsolePanel runId={runId} live />}
      </TabsContent>
    </Tabs>
  );
}

/** The Code tab of a run: this session's main and each worker's checkout, refreshed on edits. */
function RunCode({ runId, projectId, focus }: {
  runId: string;
  projectId?: string;
  focus: OpenCodeDetail | null;
}) {
  const live = useRunStore((s) => s.live);
  const names = useRunStore((s) => s.names);
  const toolFeed = useRunStore((s) => s.toolFeed);
  const agents = useRunStore((s) => s.agents);
  const editKeys = useMemo(
    () => Object.fromEntries(Object.entries(toolFeed).map(([r, f]) => [r, lastEditId(f)])),
    [toolFeed],
  );
  const working = useMemo(
    () => Object.entries(agents).filter(([, a]) => a.state === "running").map(([r]) => r),
    [agents],
  );
  // Opens on the worker who is writing code right now (following its edits), else the session.
  const [initial] = useState<CodeSource>(() =>
    live && working[0] ? { kind: "role", role: working[0] } : { kind: "run" });
  return (
    <CodeBrowser runId={runId} projectId={projectId} live={live} editKeys={editKeys}
                 names={names} working={working} focus={focus} initialSource={initial} />
  );
}
