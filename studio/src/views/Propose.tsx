import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, harnessApi, metaApi, projectsApi } from "../api";
import { PlaybookPanel } from "../features/team/PlaybookPanel";
import { TeamGraph } from "../features/team/TeamGraph";
import { CapabilitySwitches } from "../components/CapabilitySwitches";
import { CostStrip } from "../components/CostStrip";
import { WorkerCard } from "../components/WorkerCard";
import type {
  CapabilitiesMeta,
  CapabilityBlock,
  HarnessAgentConfig,
  HarnessKind,
  HarnessProjectConfig,
  RoleCapState,
} from "../lib/capabilities";
import type {
  AgentConfig,
  MetaProposal,
  ModelTier,
  PersonaHire,
  PlaybookEntry,
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

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.detail : String(e);
}

export function Propose() {
  const [searchParams] = useSearchParams();
  const initialProjectId = searchParams.get("project_id") ?? "";
  const initialPriorRunId = searchParams.get("prior_run_id") ?? "";

  const [goal, setGoal] = useState("");
  const [metaModel, setMetaModel] = useState<ModelTier>("heavy");
  const [proposal, setProposal] = useState<MetaProposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // For continuation flow, the project_id comes pre-set via query
  // string — the meta-agent reads inputs/ from data/projects/{id}/
  const [projectId, setProjectId] = useState(initialProjectId);
  const navigate = useNavigate();
  // Harness selector: "legacy" stays the default; "opencode" reveals the
  // per-role capability switches (plan §5.7).
  const [harness, setHarness] = useState<HarnessKind>("legacy");
  const [capMeta, setCapMeta] = useState<CapabilitiesMeta | null>(null);
  const [roleCaps, setRoleCaps] = useState<Record<string, RoleCapState>>({});
  const [view, setView] = useState<"graph" | "list">("graph");
  const [playbookUsed, setPlaybookUsed] = useState<PlaybookEntry[]>([]);
  const capSeq = useRef<Record<string, number>>({});

  // Load the toggle levels once the opencode harness is chosen.
  useEffect(() => {
    if (harness !== "opencode" || capMeta) return;
    let cancelled = false;
    harnessApi
      .capabilities()
      .then((m) => !cancelled && setCapMeta(m))
      .catch((e) => !cancelled && setError(errorText(e)));
    return () => {
      cancelled = true;
    };
  }, [harness, capMeta]);

  // Resolve role type + default capabilities for each proposed agent.
  useEffect(() => {
    if (harness !== "opencode" || !capMeta || !proposal) return;
    for (const agent of proposal.agents) {
      if (capSeq.current[agent.role]) continue;
      void resolveRole(agent);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [harness, capMeta, proposal]);

  async function resolveRole(agent: AgentConfig, caps?: CapabilityBlock, roleType?: string) {
    const seq = (capSeq.current[agent.role] = (capSeq.current[agent.role] ?? 0) + 1);
    try {
      const r = await harnessApi.resolve({
        role: agent.role,
        role_type: roleType ?? agent.agent_class_role_type ?? undefined,
        is_reviewer: !!agent.is_reviewer,
        model_id: agent.model_id || undefined,
        capabilities: caps,
      });
      if (capSeq.current[agent.role] !== seq) return;
      setRoleCaps((prev) => ({
        ...prev,
        [agent.role]: {
          roleType: r.role_type,
          capabilities: r.capabilities,
          visionAvailable: r.vision_available,
          error: null,
        },
      }));
    } catch (e) {
      if (capSeq.current[agent.role] !== seq) return;
      // Keep the user's edit visible, but show why the server refused it.
      setRoleCaps((prev) => {
        const cur = prev[agent.role];
        if (!cur) return prev;
        return { ...prev, [agent.role]: { ...cur, error: errorText(e) } };
      });
    }
  }

  function handleCapChange(agent: AgentConfig, next: CapabilityBlock) {
    const cur = roleCaps[agent.role];
    if (!cur) return;
    setRoleCaps((prev) => ({ ...prev, [agent.role]: { ...cur, capabilities: next, error: null } }));
    void resolveRole(agent, next, cur.roleType);
  }

  async function handlePropose() {
    setError(null);
    setLoading(true);
    try {
      const opts: { projectId?: string; priorRunId?: string; metaModel?: ModelTier } = {};
      if (initialProjectId) opts.projectId = initialProjectId;
      if (initialPriorRunId) opts.priorRunId = initialPriorRunId;
      opts.metaModel = metaModel;
      const p = await metaApi.propose(goal, opts);
      capSeq.current = {};
      setRoleCaps({});
      setProposal(p);
      setPlaybookUsed([]);
      void loadPlaybook(p);
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

  /** Entries the meta-agent was shown; empty when there is no project or playbook yet. */
  async function loadPlaybook(p: MetaProposal) {
    const ids = p.playbook_entries_used ?? [];
    if (!initialProjectId || ids.length === 0) return;
    try {
      const pb = await projectsApi.playbook(initialProjectId);
      setPlaybookUsed(pb.entries.filter((e) => ids.includes(e.id)));
    } catch {
      // The playbook panel is informational; a failed fetch must not block approval.
      setPlaybookUsed([]);
    }
  }

  function handleTierChange(role: string, tier: ModelTier) {
    setProposal((prev) =>
      prev
        ? {
            ...prev,
            agents: prev.agents.map((a) =>
              a.role === role ? { ...a, model_tier: tier, model_id: undefined } : a,
            ),
          }
        : prev,
    );
  }

  async function handleApprove() {
    if (!proposal) return;
    // Same place the screen already saves agent fields: opencode projects
    // carry per-agent role_type + capabilities, which serialise_project reads.
    const agents: HarnessAgentConfig[] = proposal.agents.map((a) => {
      const rc = harness === "opencode" ? roleCaps[a.role] : undefined;
      return rc ? { ...a, role_type: rc.roleType, capabilities: rc.capabilities } : a;
    });
    const project: HarnessProjectConfig = {
      project_id: projectId,
      goal,
      agents,
      rbac: proposal.rbac,
      max_iterations: 5,
      ...(harness === "opencode" ? { harness } : {}),
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

  const capsByRole: Record<string, CapabilityBlock> = {};
  if (harness === "opencode") {
    for (const [role, rc] of Object.entries(roleCaps)) capsByRole[role] = rc.capabilities;
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
        <div style={{ marginBottom: 12 }}>
          <label className="small">Meta-agent model</label>
          <select
            value={metaModel}
            onChange={(e) => setMetaModel(e.target.value as ModelTier)}
            style={{ width: "auto", marginLeft: 8 }}
          >
            <option value="heavy">Heavy (default)</option>
            <option value="solid">Solid</option>
            <option value="fast">Fast</option>
          </select>
        </div>
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

          {deltas.length > 0 && <DeltaTable deltas={deltas} />}

          <div style={{ margin: "12px 0" }}>
            <label className="small" htmlFor="harness-select">Agent harness</label>
            <select
              id="harness-select"
              value={harness}
              onChange={(e) => setHarness(e.target.value as HarnessKind)}
              style={{ width: "auto", marginLeft: 8 }}
            >
              <option value="legacy">Legacy</option>
              <option value="opencode">opencode</option>
            </select>
            {harness === "opencode" && (
              <span className="muted small" style={{ marginLeft: 8 }}>
                Review what each agent may do before approving.
              </span>
            )}
          </div>

          <div className="my-3 flex gap-1" role="group" aria-label="Team view">
            <button
              type="button"
              className={view === "graph" ? "primary" : ""}
              aria-pressed={view === "graph"}
              onClick={() => setView("graph")}
            >
              Graph
            </button>
            <button
              type="button"
              className={view === "list" ? "primary" : ""}
              aria-pressed={view === "list"}
              onClick={() => setView("list")}
            >
              List
            </button>
          </div>

          {view === "graph" ? (
            <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
              <TeamGraph
                projectId={projectId}
                goal={goal}
                agents={proposal.agents}
                roleCaps={harness === "opencode" ? roleCaps : {}}
                capMeta={harness === "opencode" ? capMeta : null}
                onCapabilityChange={handleCapChange}
                onTierChange={handleTierChange}
              />
              <PlaybookPanel entries={playbookUsed} />
            </div>
          ) : (
            <>
              <CostStrip
                project={{
                  project_id: projectId,
                  goal,
                  agents: proposal.agents,
                  rbac: proposal.rbac,
                  max_iterations: 5,
                }}
                capsByRole={capsByRole}
              />
          <div className="grid-2">
            {proposal.agents.map((a) => {
              const rc = roleCaps[a.role];
              return (
                <div key={a.role}>
                  <WorkerCard agent={a} mode="preview" />
                  {harness === "opencode" && capMeta && rc && (
                    <CapabilitySwitches
                      role={a.role}
                      roleType={rc.roleType}
                      value={rc.capabilities}
                      onChange={(next) => handleCapChange(a, next)}
                      levels={capMeta.levels}
                      defaults={capMeta.defaults[rc.roleType]}
                      visionAvailable={rc.visionAvailable}
                      production={capMeta.production}
                      hostDevWarning={capMeta.host_dev_warning}
                      error={rc.error}
                    />
                  )}
                </div>
              );
            })}
          </div>

            </>
          )}

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
