import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError, invokeApi, projectsApi, runsApi } from "../api";
import { PersonaSlideOver } from "../components/PersonaSlideOver";
import { WorkerSettingsPanel } from "../components/WorkerSettingsPanel";
import { CostStrip } from "../components/CostStrip";
import { PipelineGraph } from "../components/PipelineGraph";
import { RoadmapTab } from "../components/RoadmapTab";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "../components/ui/dialog";
import { Alert, Input, Label, Page, SectionTitle, Textarea, linkClass } from "../components/ui/form-controls";
import { Segmented } from "../components/ui/segmented";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import { checkCls } from "../lib/field";
import { cn } from "../lib/utils";
import type { AgentConfig, ProjectConfig, RunMeta } from "../types";
import { ProjectInsights } from "../features/project/ProjectInsights";
import { SeatChips } from "../features/project/SeatChips";
import { SessionsTab } from "../features/project/SessionsTab";
import { PROJECT_TABS, TAB_LABEL, normalizeTab, type ProjectTab } from "../features/project/projectTabs";
import { useDetailLevel, type DetailLevel } from "../features/project/useDetailLevel";
import { ProjectWork, type RunRequest } from "../features/work/ProjectWork";
import { WorkSettings } from "../features/work/WorkSettings";
import { Inputs } from "./Inputs";
import { PlanTab } from "../features/plan/PlanTab";
import { SeatsPanel } from "../features/team/SeatsPanel";
import { RulesPanel } from "../features/team/RulesPanel";

const DETAIL_OPTIONS: { value: DetailLevel; label: string }[] = [
  { value: "overview", label: "Overview" },
  { value: "technical", label: "Technical" },
];

const DEFAULT_GATES = {
  pre_run_cost_preview: true,
  heavy_tier_mid_run_dispatch: true,
  iteration_breach_at: 2 as number | null,
  per_iteration: false,
  token_budget_usd: null as number | null,
};

export function Studio() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<ProjectConfig | null>(null);
  const [runs, setRuns] = useState<RunMeta[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // The project is ongoing work (plan §13): its backlog and review queue come first.
  // The tab lives in `?tab=` so links work; old names (inputs, roadmap, insights) map onto new tabs.
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: ProjectTab = normalizeTab(searchParams.get("tab"));
  function setTab(next: ProjectTab) {
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        p.set("tab", next);
        return p;
      },
      { replace: true },
    );
  }
  const [detail, setDetail] = useDetailLevel(id ?? "");
  const [settingsRole, setSettingsRole] = useState<string | null>(null);
  const [slideOverPersonaId, setSlideOverPersonaId] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!id) return;
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function load() {
    if (!id) return;
    try {
      const [p, rs] = await Promise.all([
        projectsApi.get(id),
        runsApi.list(id),
      ]);
      setProject(p);
      setRuns(rs);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  function updateAgent(role: string, partial: Partial<AgentConfig>) {
    if (!project) return;
    const next: ProjectConfig = {
      ...project,
      agents: (project.agents ?? []).map((a) =>
        a.role === role ? { ...a, ...partial } : a,
      ),
    };
    setProject(next);
  }

  function addAgent() {
    if (!project) return;
    const role = `agent_${(project.agents?.length ?? 0) + 1}`;
    setProject({
      ...project,
      agents: [
        ...(project.agents ?? []),
        {
          role,
          description: "",
          depends_on: [],
          declared_local_tools: ["fs-read"],
          model_tier: "solid",
          max_tool_iterations: 6,
        },
      ],
    });
  }

  function removeAgent(role: string) {
    if (!project) return;
    setProject({
      ...project,
      agents: (project.agents ?? []).filter((a) => a.role !== role),
    });
  }

  async function save() {
    if (!project || !id) return;
    setSaving(true);
    try {
      await projectsApi.update(id, project);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSaving(false);
    }
  }

  // Phase C.0.5: pre_run_cost_preview gate may intercept the first
  // POST. We open a confirm modal showing the estimate, then re-POST
  // with confirm=true.
  const [costGate, setCostGate] = useState<null | {
    estimate: { total_usd: number };
    message: string;
  }>(null);

  // What the run should take on (backlog items, a pending run to continue);
  // kept across the cost-preview round trip.
  const [runRequest, setRunRequest] = useState<RunRequest>({});
  const [starting, setStarting] = useState(false);

  async function runNow(confirm = false, request: RunRequest = runRequest) {
    if (!id || !project) return;
    // Always persist current agent config before invoking so model_tier
    // changes and other edits made in the UI are actually used by the run.
    if (!confirm) {
      setRunRequest(request);
      await save();
    }
    setStarting(true);
    try {
      const res = await invokeApi.invoke(id, { ...request, ...(confirm ? { confirm: true } : {}) });
      if (res.accepted) {
        setCostGate(null);
        navigate(`/runs/${res.run_id}`);
        return;
      }
      // Cost preview gate intercepted — surface the estimate.
      setCostGate({
        estimate: res.estimated_cost,
        message: res.message,
      });
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setStarting(false);
    }
  }

  if (!project) {
    return (
      <Page>
        {error ? <Alert variant="error">{error}</Alert> : <p className="text-muted-foreground">loading…</p>}
        <Link className={linkClass} to="/projects">← projects</Link>
      </Page>
    );
  }

  return (
    <Page>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="m-0 text-[22px] font-semibold tracking-tight">{project.project_id}</h1>
          <SeatChips seats={project.seats} />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Segmented label="Detail level" value={detail} onChange={setDetail} options={DETAIL_OPTIONS} />
          <Button variant="outline" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
          {/* Backlog runs start from the Work tab; this runs on the project goal alone. */}
          <Button variant="outline" onClick={() => void runNow(false, {})}>Run on the goal only</Button>
        </div>
      </div>

      {error && <Alert variant="error" className="mb-3">{error}</Alert>}

      <Tabs value={tab} onValueChange={(v) => setTab(v as ProjectTab)} className="mb-4">
        <TabsList>
          {PROJECT_TABS.map((t) => (
            <TabsTrigger key={t} value={t}>{TAB_LABEL[t]}</TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {tab === "work" ? (
        id && <ProjectWork projectId={id} seats={project.seats} starting={starting} onStartRun={(r) => void runNow(false, r)} />
      ) : tab === "team" ? (
        <>
          <SeatsPanel project={project} onSaved={setProject} />
          {id && <RulesPanel projectId={id} />}
          <Card className="mb-3 p-4">
            <Label>Goal</Label>
            <Textarea
              rows={2}
              value={project.goal}
              onChange={(e) => setProject({ ...project, goal: e.target.value })}
            />
            <div className="mt-3 grid grid-cols-3 gap-4">
              <div>
                <Label>Max iterations</Label>
                <Input
                  type="number"
                  min={1}
                  max={20}
                  value={project.max_iterations ?? 5}
                  onChange={(e) =>
                    setProject({
                      ...project,
                      max_iterations: parseInt(e.target.value || "5", 10),
                    })
                  }
                />
              </div>
            </div>
            {(() => {
              const prMode = project.pr_mode ?? "manual";
              const opts: { value: "manual" | "auto"; title: string; hint: string }[] = [
                { value: "manual", title: "Manual", hint: "Stop at an integration branch; you open the PR. Recommended for real repos." },
                { value: "auto",   title: "Auto",   hint: "Merge straight into main. Sandbox / throwaway projects only." },
              ];
              return (
                <div className="mt-4">
                  <Label className="mb-1.5">PR mode</Label>
                  <div className="grid grid-cols-2 gap-2">
                    {opts.map((o) => {
                      const selected = prMode === o.value;
                      return (
                        <button
                          key={o.value}
                          type="button"
                          onClick={() => setProject({ ...project, pr_mode: o.value })}
                          className={cn(
                            "flex cursor-pointer flex-col gap-0.5 rounded-md border bg-card px-3 py-2.5 text-left text-card-foreground",
                            selected ? "border-status-complete bg-status-complete/10" : "border-border",
                          )}
                        >
                          <span className="text-[13px] font-semibold">
                            {selected ? "● " : "○ "}{o.title}
                          </span>
                          <span className="text-xs leading-tight text-muted-foreground">
                            {o.hint}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })()}
          </Card>

          <CostStrip project={project} />

          <SectionTitle>Agents</SectionTitle>
          {/* Phase G.1 — DAG preview. Click a node to open agent settings. */}
          {(project.agents ?? []).length > 0 && (
            <PipelineGraph
              agents={project.agents ?? []}
              onClickAgent={(role) => setSettingsRole(role)}
              onRemoveAgent={removeAgent}
            />
          )}
          <Button variant="outline" className="mt-2" onClick={addAgent}>+ Add agent</Button>
        </>
      ) : tab === "plan" ? (
        id && (
          <>
            <PlanTab projectId={id} />
            <SectionTitle className="mt-8">Notes</SectionTitle>
            <RoadmapTab projectId={id} />
            <SectionTitle className="mt-6">Files</SectionTitle>
            <Inputs projectId={id} />
          </>
        )
      ) : tab === "sessions" ? (
        <SessionsTab runs={runs} onResumed={() => void load()} />
      ) : tab === "record" ? (
        id && <ProjectInsights projectId={id} />
      ) : (
        <>
          <SettingsTab
            gates={{ ...DEFAULT_GATES, ...(project.approval_gates ?? {}) }}
            onChange={(next) =>
              setProject({
                ...project,
                approval_gates: next,
              })
            }
          />
          <WorkSettings
            gates={project.approval_gates ?? {}}
            onChange={(patch) =>
              setProject({ ...project, approval_gates: { ...(project.approval_gates ?? {}), ...patch } })
            }
          />
        </>
      )}

      {/* Phase C.0.5: pre-run cost preview gate. Studio shows the
          estimate and either confirms or backs out. */}
      {costGate && (
        <Dialog open onOpenChange={(o) => !o && setCostGate(null)}>
          <DialogContent>
            <DialogTitle>Confirm run</DialogTitle>
            <DialogDescription>{costGate.message}</DialogDescription>
            <Card className="border-status-running/40 bg-status-running/10 p-4">
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">Estimated worst-case cost</span>
                <strong className="text-lg">
                  ${costGate.estimate.total_usd.toFixed(2)}
                </strong>
              </div>
            </Card>
            <p className="m-0 text-xs text-muted-foreground">
              This is a worst-case figure (every agent runs every iteration to
              max). Real spend is usually lower. To turn this gate off, edit
              the project's approval gates settings.
            </p>
            <DialogFooter className="justify-start">
              <Button onClick={() => void runNow(true)}>Confirm and run</Button>
              <Button variant="outline" onClick={() => setCostGate(null)}>Cancel</Button>
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

      {/* WorkerSettingsPanel — opens when gear is clicked on a WorkerCard */}
      {settingsRole && project && (() => {
        const agent = (project.agents ?? []).find((a) => a.role === settingsRole);
        if (!agent) return null;
        return (
          <WorkerSettingsPanel
            agent={agent}
            allRoles={(project.agents ?? []).map((a) => a.role)}
            projectRbacScopes={(project.rbac ?? {})[settingsRole]?.allowed_scopes ?? []}
            onClose={() => setSettingsRole(null)}
            onChange={(partial) => updateAgent(settingsRole, partial)}
            onSave={async (partial) => {
              if (!id) return;
              const latest = {
                ...project,
                agents: (project.agents ?? []).map((a) =>
                  a.role === settingsRole ? { ...a, ...partial } : a,
                ),
              };
              await projectsApi.update(id, latest);
            }}
            onRbacChange={(add, remove) => {
              if (!id) return;
              const rbac = { ...(project.rbac ?? {}) };
              const current = rbac[settingsRole]?.allowed_scopes ?? [];
              const next = [...new Set([...current.filter((s) => !remove.includes(s)), ...add])];
              rbac[settingsRole] = { allowed_scopes: next };
              const updated = { ...project, rbac };
              setProject(updated);
              void projectsApi.update(id, updated);
            }}
          />
        );
      })()}
    </Page>
  );
}


// ── Settings tab (Phase C.0.5: approval gates) ────────────────────────────

interface SettingsTabProps {
  gates: typeof DEFAULT_GATES;
  onChange: (next: typeof DEFAULT_GATES) => void;
}

function SettingsTab({ gates, onChange }: SettingsTabProps) {
  return (
    <Card className="mb-3 p-4">
      <h2 className="m-0 text-base font-semibold">Approval gates</h2>
      <p className="my-2 text-xs text-muted-foreground">
        Pause and confirm before the team spends tokens on big actions.
        These changes save with the project on the next click of Save.
      </p>

      <div className="mt-3 flex flex-col gap-3">
        <Toggle
          label="Pre-run cost preview"
          help="Show the cost estimate before each run starts. Click Confirm to proceed."
          checked={gates.pre_run_cost_preview}
          onChange={(v) => onChange({ ...gates, pre_run_cost_preview: v })}
        />
        <Toggle
          label="Confirm HEAVY-tier mid-run dispatch"
          help="Pause before tech_lead / architect (HEAVY tier) is auto-spawned mid-run."
          checked={gates.heavy_tier_mid_run_dispatch}
          onChange={(v) => onChange({ ...gates, heavy_tier_mid_run_dispatch: v })}
        />
        <NumberOrOff
          label="Iteration-breach threshold"
          help="Pause when the scheduler would respawn the rejected agent for iteration ≥ N. Off disables."
          value={gates.iteration_breach_at}
          onChange={(v) => onChange({ ...gates, iteration_breach_at: v })}
        />
        <Toggle
          label="Confirm every iteration"
          help="Heavy-handed; opt-in for high-stakes runs."
          checked={gates.per_iteration}
          onChange={(v) => onChange({ ...gates, per_iteration: v })}
        />
        <NumberOrOff
          label="Token-budget pause ($)"
          help="Pause when cumulative cost crosses this dollar amount. Off disables."
          value={gates.token_budget_usd}
          onChange={(v) => onChange({ ...gates, token_budget_usd: v })}
          step={0.5}
        />
      </div>
    </Card>
  );
}

const ROW_CLS = "rounded-md border border-border bg-muted px-3 py-2";
const CHECK_LABEL = "mb-0 inline-flex cursor-pointer items-center gap-2 text-sm font-medium text-foreground";

function Toggle(props: {
  label: string;
  help: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className={ROW_CLS}>
      <label className={CHECK_LABEL}>
        <input
          type="checkbox"
          className={checkCls}
          checked={props.checked}
          onChange={(e) => props.onChange(e.target.checked)}
        />
        {props.label}
      </label>
      <p className="mb-0 ml-6 mt-1 text-xs text-muted-foreground">{props.help}</p>
    </div>
  );
}

function NumberOrOff(props: {
  label: string;
  help: string;
  value: number | null;
  onChange: (v: number | null) => void;
  step?: number;
}) {
  const enabled = props.value !== null;
  return (
    <div className={ROW_CLS}>
      <label className={CHECK_LABEL}>
        <input
          type="checkbox"
          className={checkCls}
          checked={enabled}
          onChange={(e) => props.onChange(e.target.checked ? (props.value ?? 2) : null)}
        />
        {props.label}
      </label>
      {enabled && (
        <Input
          type="number"
          step={props.step ?? 1}
          min={0}
          value={props.value ?? 0}
          onChange={(e) => props.onChange(parseFloat(e.target.value || "0"))}
          className="ml-6 mt-1 w-[100px]"
        />
      )}
      <p className="mb-0 ml-6 mt-1 text-xs text-muted-foreground">{props.help}</p>
    </div>
  );
}
