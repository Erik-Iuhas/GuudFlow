import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, metaApi, projectsApi } from "../api";
import { CostStrip } from "../components/CostStrip";
import type {
  MetaProposal,
  PersonaHire,
  ProjectConfig,
  RoleDelta,
} from "../types";

// Match `path/to/file.ext` and bare `file.ext` references so the
// rationale can highlight things the meta-agent cited from the inputs
// (`inputs/uploads/spec.pdf`, `src/auth.py`, etc.). Conservative regex
// so prose like "the agent" doesn't accidentally match.
const PATH_LIKE = /(\b[\w./-]*\/?[\w./-]+\.[a-zA-Z0-9]{1,6}\b)/g;

/** Render rationale prose with file paths highlighted as code spans. */
function highlightPaths(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  for (const match of text.matchAll(PATH_LIKE)) {
    const idx = match.index ?? 0;
    if (idx > lastIndex) {
      parts.push(text.slice(lastIndex, idx));
    }
    parts.push(
      <code key={`p-${idx}`} className="path-mention">
        {match[1]}
      </code>,
    );
    lastIndex = idx + match[1].length;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}

export function Propose() {
  const [searchParams] = useSearchParams();
  const initialProjectId = searchParams.get("project_id") ?? "";
  const initialPriorRunId = searchParams.get("prior_run_id") ?? "";

  const [goal, setGoal] = useState("");
  const [proposal, setProposal] = useState<MetaProposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // For continuation flow, the project_id comes pre-set via query
  // string — the meta-agent reads inputs/ from data/projects/{id}/
  const [projectId, setProjectId] = useState(initialProjectId);
  const navigate = useNavigate();

  async function handlePropose() {
    setError(null);
    setLoading(true);
    try {
      const opts: { projectId?: string; priorRunId?: string } = {};
      if (initialProjectId) opts.projectId = initialProjectId;
      if (initialPriorRunId) opts.priorRunId = initialPriorRunId;
      const p = await metaApi.propose(goal, opts);
      setProposal(p);
      // Suggest a project id from the goal slug ONLY when this is a
      // greenfield proposal. Continuation flow keeps the existing id.
      if (!initialProjectId) {
        setProjectId(slugify(goal));
      }
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
      if (initialProjectId) {
        // Continuation: update the existing project's team config.
        await projectsApi.update(projectId, project);
      } else {
        await projectsApi.create(project);
      }
      navigate(`/projects/${projectId}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  const isContinuation = !!initialPriorRunId;
  const hires = proposal?.persona_hires ?? [];
  const deltas = proposal?.deltas ?? [];
  const warnings = proposal?.warnings ?? [];

  return (
    <div className="page">
      <h1>{isContinuation ? "Continue from a prior run" : "Propose a project"}</h1>
      <p className="muted small">
        {isContinuation
          ? "Continuation runs read the prior team + outputs and propose what to change. Approve the deltas below to update the project's stored team."
          : "Describe the goal in plain English. The meta-agent reads any uploads + source repo you've attached and proposes a tailored team."}
      </p>

      {error && <div className="error">{error}</div>}

      <div className="card">
        <label>{isContinuation ? "What's the new ask?" : "Project goal"}</label>
        <textarea
          rows={3}
          placeholder={
            isContinuation
              ? "e.g. Add a refresh-token endpoint to the auth flow we shipped last run."
              : "e.g. Build an auth flow with JWT refresh tokens for our existing FastAPI backend"
          }
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
          <p className="rationale">{highlightPaths(proposal.rationale)}</p>

          {warnings.length > 0 && (
            <div className="warn">
              <strong>Notes from the meta-agent:</strong>
              <ul>
                {warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          <CostStrip
            project={{
              project_id: projectId,
              goal,
              agents: proposal.agents,
              rbac: proposal.rbac,
              max_iterations: 5,
            }}
          />

          {deltas.length > 0 && <DeltaTable deltas={deltas} />}

          <hr />
          <div className="grid-2">
            {proposal.agents.map((a) => {
              const hire = hires.find((h) => h.role === a.role);
              return (
                <div key={a.role} className="card agent-card">
                  <div className="role">{a.role}</div>
                  <div className="muted small">{a.description}</div>
                  <div className="tier-row">
                    <span className={`tag ${a.model_tier ?? "solid"}`}>
                      {a.model_tier ?? "solid"}
                    </span>
                    {a.is_reviewer && <span className="tag">reviewer</span>}
                    {a.is_final_reviewer && (
                      <span className="tag">final reviewer</span>
                    )}
                  </div>
                  {hire && (
                    <div className="hire-row">
                      <span
                        className={`tag tag-hire-${hire.action}`}
                      >
                        {hire.action === "create_new" ? "new" : "re-hire"}
                      </span>
                      <strong>{hire.display_name}</strong>
                      <span className="mono small muted">
                        {hire.persona_id}
                      </span>
                    </div>
                  )}
                  <div className="muted small">
                    tools:{" "}
                    {(a.declared_local_tools ?? []).join(", ") || "(none)"}
                  </div>
                  {a.depends_on && a.depends_on.length > 0 && (
                    <div className="muted small">
                      depends on: {a.depends_on.join(", ")}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <hr />
          <div className="row">
            <div style={{ flex: 1 }}>
              <label>Project id</label>
              <input
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                disabled={isContinuation}
              />
            </div>
            <button
              className="primary"
              disabled={!projectId}
              onClick={handleApprove}
              style={{ alignSelf: "flex-end" }}
            >
              {isContinuation ? "Approve deltas" : "Approve & create"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function DeltaTable({ deltas }: { deltas: RoleDelta[] }) {
  return (
    <div className="card delta-card">
      <h3 style={{ marginTop: 0 }}>What's changing</h3>
      <table className="list">
        <thead>
          <tr>
            <th>Role</th>
            <th>Action</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {deltas.map((d) => (
            <tr key={`${d.role}-${d.action}`}>
              <td className="mono">{d.role}</td>
              <td>
                <span className={`tag tag-delta-${d.action}`}>
                  {d.action.replace("_", " ")}
                </span>
              </td>
              <td className="small muted">{deltaDetail(d)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function deltaDetail(d: RoleDelta): string {
  switch (d.action) {
    case "keep":
      return "Unchanged — same role, same persona, same model tier";
    case "drop":
      return "Removed — work is locked in or no longer relevant";
    case "add":
      return `New — tier ${d.to_state?.model_tier ?? "?"}`;
    case "swap_tier":
      return `Tier ${d.from_state?.model_tier ?? "?"} → ${d.to_state?.model_tier ?? "?"}`;
    case "replace_persona":
      return `Persona ${d.from_state?.persona_id ?? "(none)"} → ${d.to_state?.persona_id ?? "(none)"}`;
    default:
      return "";
  }
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}
