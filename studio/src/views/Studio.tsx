import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, invokeApi, projectsApi, runsApi } from "../api";
import { AgentCard } from "../components/AgentCard";
import { CostStrip } from "../components/CostStrip";
import { PipelineGraph } from "../components/PipelineGraph";
import type { AgentConfig, ProjectConfig, RunMeta } from "../types";
import { Inputs } from "./Inputs";

type Tab = "team" | "inputs" | "settings";

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
  const [tab, setTab] = useState<Tab>("team");
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
          declared_local_tools: ["fs:read"],
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

  async function runNow(confirm = false) {
    if (!id) return;
    try {
      const res = await invokeApi.invoke(id, confirm ? { confirm: true } : {});
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
    }
  }

  if (!project) {
    return (
      <div className="page">
        {error ? <div className="error">{error}</div> : <p className="muted">loading…</p>}
        <Link to="/projects">← projects</Link>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{project.project_id}</h1>
        <div className="row">
          <button onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button className="primary" onClick={() => void runNow(false)}>
            Run now
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="tabs">
        <button
          className={`tab ${tab === "team" ? "tab-active" : ""}`}
          onClick={() => setTab("team")}
        >
          Team
        </button>
        <button
          className={`tab ${tab === "inputs" ? "tab-active" : ""}`}
          onClick={() => setTab("inputs")}
        >
          Inputs
        </button>
        <button
          className={`tab ${tab === "settings" ? "tab-active" : ""}`}
          onClick={() => setTab("settings")}
        >
          Settings
        </button>
      </div>

      {tab === "team" ? (
        <>
          <div className="card">
            <label>Goal</label>
            <textarea
              rows={2}
              value={project.goal}
              onChange={(e) => setProject({ ...project, goal: e.target.value })}
            />
            <div className="grid-3" style={{ marginTop: 12 }}>
              <div>
                <label>Max iterations</label>
                <input
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
          </div>

          <CostStrip project={project} />

          <h2>Agents</h2>
          {/* Phase G.1 — DAG preview so the user can spot disconnected
              agents or weird dependencies before launching a run. */}
          {(project.agents ?? []).length > 0 && (
            <PipelineGraph agents={project.agents ?? []} />
          )}
          <div className="grid-2">
            {(project.agents ?? []).map((a) => (
              <AgentCard
                key={a.role}
                agent={a}
                allRoles={(project.agents ?? []).map((x) => x.role)}
                onChange={(partial) => updateAgent(a.role, partial)}
                onRemove={() => removeAgent(a.role)}
              />
            ))}
          </div>
          <button onClick={addAgent} style={{ marginTop: 8 }}>+ Add agent</button>

          <h2 style={{ marginTop: 24 }}>Recent runs</h2>
          {runs.length === 0 ? (
            <p className="muted small">No runs yet. Hit "Run now" to start one.</p>
          ) : (
            <table className="list">
              <thead>
                <tr>
                  <th>Run id</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Completed agents</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.run_id}>
                    <td>
                      <Link to={`/runs/${r.run_id}`} className="mono">
                        {r.run_id}
                      </Link>
                    </td>
                    <td>
                      <span className={`tag tag-status-${r.status}`}>{r.status}</span>
                    </td>
                    <td className="small muted">{r.created_at?.replace("T", " ").split(".")[0]}</td>
                    <td className="small">{(r.completed_agents ?? []).join(", ")}</td>
                    <td>
                      <Link to={`/runs/${r.run_id}`}>
                        <button>Watch</button>
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      ) : tab === "inputs" ? (
        id && <Inputs projectId={id} />
      ) : (
        <SettingsTab
          gates={{ ...DEFAULT_GATES, ...(project.approval_gates ?? {}) }}
          onChange={(next) =>
            setProject({
              ...project,
              approval_gates: next,
            })
          }
        />
      )}

      {/* Phase C.0.5: pre-run cost preview gate. Studio shows the
          estimate and either confirms or backs out. */}
      {costGate && (
        <div className="modal-backdrop" onClick={() => setCostGate(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Confirm run</h3>
            <p>{costGate.message}</p>
            <div
              className="card"
              style={{ background: "#f0f9ff", borderColor: "#bae6fd" }}
            >
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted small">Estimated worst-case cost</span>
                <strong style={{ fontSize: 18 }}>
                  ${costGate.estimate.total_usd.toFixed(2)}
                </strong>
              </div>
            </div>
            <p className="muted small" style={{ marginTop: 8 }}>
              This is a worst-case figure (every agent runs every iteration to
              max). Real spend is usually lower. To turn this gate off, edit
              the project's approval gates settings.
            </p>
            <div className="row" style={{ marginTop: 12, gap: 8 }}>
              <button
                className="primary"
                onClick={() => void runNow(true)}
              >
                Confirm and run
              </button>
              <button onClick={() => setCostGate(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


// ── Settings tab (Phase C.0.5: approval gates) ────────────────────────────

interface SettingsTabProps {
  gates: typeof DEFAULT_GATES;
  onChange: (next: typeof DEFAULT_GATES) => void;
}

function SettingsTab({ gates, onChange }: SettingsTabProps) {
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Approval gates</h2>
      <p className="muted small">
        Pause and confirm before the team spends tokens on big actions.
        These changes save with the project on the next click of Save.
      </p>

      <div className="settings-grid">
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
    </div>
  );
}

function Toggle(props: {
  label: string;
  help: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="settings-row">
      <label>
        <input
          type="checkbox"
          checked={props.checked}
          onChange={(e) => props.onChange(e.target.checked)}
        />{" "}
        {props.label}
      </label>
      <p className="muted small">{props.help}</p>
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
    <div className="settings-row">
      <label>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => props.onChange(e.target.checked ? (props.value ?? 2) : null)}
        />{" "}
        {props.label}
      </label>
      {enabled && (
        <input
          type="number"
          step={props.step ?? 1}
          min={0}
          value={props.value ?? 0}
          onChange={(e) => props.onChange(parseFloat(e.target.value || "0"))}
          style={{ marginTop: 4, width: 100 }}
        />
      )}
      <p className="muted small">{props.help}</p>
    </div>
  );
}
