import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, metaApi, projectsApi } from "../api";
import { CostStrip } from "../components/CostStrip";
import type { MetaProposal, ProjectConfig } from "../types";

export function Propose() {
  const [goal, setGoal] = useState("");
  const [proposal, setProposal] = useState<MetaProposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [projectId, setProjectId] = useState("");
  const navigate = useNavigate();

  async function handlePropose() {
    setError(null);
    setLoading(true);
    try {
      const p = await metaApi.propose(goal);
      setProposal(p);
      // Suggest a project id from the goal slug.
      setProjectId(slugify(goal));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleApprove() {
    if (!proposal) return;
    const project: ProjectConfig = {
      project_id: projectId,
      goal,
      agents: proposal.agents,
      rbac: proposal.rbac,
      max_iterations: 5,
    };
    try {
      await projectsApi.create(project);
      navigate(`/projects/${projectId}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <div className="page">
      <h1>Propose a project</h1>
      <p className="muted small">
        Describe the goal in plain English. The meta-agent proposes a starting
        team (developer + reviewer baseline today; richer proposals when the
        meta-agent's full LLM-driven path lands).
      </p>

      {error && <div className="error">{error}</div>}

      <div className="card">
        <label>Project goal</label>
        <textarea
          rows={3}
          placeholder="e.g. Build an auth flow with JWT refresh tokens for our existing FastAPI backend"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
        />
        <div className="row-end" style={{ marginTop: 12 }}>
          <button
            className="primary"
            disabled={!goal || loading}
            onClick={handlePropose}
          >
            {loading ? "Proposing…" : "Propose team"}
          </button>
        </div>
      </div>

      {proposal && (
        <div className="card">
          <h2>Proposed team</h2>
          <p className="small muted">{proposal.rationale}</p>

          <CostStrip
            project={{
              project_id: projectId,
              goal,
              agents: proposal.agents,
              rbac: proposal.rbac,
              max_iterations: 5,
            }}
          />

          <hr />
          <div className="grid-2">
            {proposal.agents.map((a) => (
              <div key={a.role} className="card agent-card">
                <div className="role">{a.role}</div>
                <div className="muted small">{a.description}</div>
                <div className="tier-row">
                  <span className={`tag ${a.model_tier ?? "solid"}`}>
                    {a.model_tier ?? "solid"}
                  </span>
                  {a.is_reviewer && <span className="tag">reviewer</span>}
                </div>
                <div className="muted small">
                  tools: {(a.declared_local_tools ?? []).join(", ") || "(none)"}
                </div>
                {a.depends_on && a.depends_on.length > 0 && (
                  <div className="muted small">
                    depends on: {a.depends_on.join(", ")}
                  </div>
                )}
              </div>
            ))}
          </div>

          <hr />
          <div className="row">
            <div style={{ flex: 1 }}>
              <label>Project id</label>
              <input
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
              />
            </div>
            <button
              className="primary"
              disabled={!projectId}
              onClick={handleApprove}
              style={{ alignSelf: "flex-end" }}
            >
              Approve & create
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}
