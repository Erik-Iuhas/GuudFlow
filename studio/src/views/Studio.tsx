import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, invokeApi, projectsApi, runsApi } from "../api";
import { AgentCard } from "../components/AgentCard";
import { CostStrip } from "../components/CostStrip";
import type { AgentConfig, ProjectConfig, RunMeta } from "../types";
import { Inputs } from "./Inputs";

type Tab = "team" | "inputs";

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

  async function runNow() {
    if (!id) return;
    try {
      const res = await invokeApi.invoke(id, {});
      navigate(`/runs/${res.run_id}`);
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
          <button className="primary" onClick={runNow}>
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
      ) : (
        id && <Inputs projectId={id} />
      )}
    </div>
  );
}
