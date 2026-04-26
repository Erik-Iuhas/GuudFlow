import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, projectsApi } from "../api";
import type { ProjectConfig } from "../types";

export function Projects() {
  const [projects, setProjects] = useState<ProjectConfig[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [blankOpen, setBlankOpen] = useState(false);
  const [blankId, setBlankId] = useState("");
  const [blankGoal, setBlankGoal] = useState("");
  const [blankBusy, setBlankBusy] = useState(false);
  const [demoBusy, setDemoBusy] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    void load();
  }, []);

  // Close the menu when clicking outside.
  useEffect(() => {
    if (!menuOpen) return;
    function onClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

  async function load() {
    try {
      setProjects(await projectsApi.list());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm(`Delete project ${id}?`)) return;
    const prev = projects;
    setProjects(projects.filter((p) => p.project_id !== id));
    try {
      await projectsApi.remove(id);
    } catch (e) {
      setProjects(prev);
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function createDemo() {
    // Phase G.2 — first-timer's "try this" path. Posts a curated
    // demo project with the seed personas attached. The user lands
    // in Studio ready to invoke a run.
    setDemoBusy(true);
    try {
      const created = await projectsApi.create({
        project_id: "demo-hello-python",
        goal: "Add a hello.py module that exports greet(name) returning 'Hello, {name}!'",
        agents: [
          {
            role: "developer",
            description: "Writes the code",
            system_prompt: "Write a small Python module. Use fs:write to land hello.py with greet(name) returning the right format.",
            declared_local_tools: ["fs:read", "fs:list", "fs:write"],
            persona_id: "mike-frontend-v1",
            model_tier: "fast",
          },
          {
            role: "reviewer",
            description: "Reviews the developer's work",
            depends_on: ["developer"],
            declared_local_tools: ["fs:read", "fs:list", "git:diff", "git:log"],
            persona_id: "alex-code_reviewer-v1",
            model_tier: "fast",
            agent_class_role_type: "code_reviewer",
            is_reviewer: true,
            max_tool_iterations: 5,
          },
        ],
        rbac: {
          developer: { allowed_scopes: ["fs:write"] },
          reviewer: { allowed_scopes: [] },
        },
        max_iterations: 3,
      });
      navigate(`/projects/${created.project_id}`);
    } catch (e) {
      // If the project already exists (re-clicking demo), just navigate.
      if (e instanceof ApiError && e.status === 409) {
        navigate(`/projects/demo-hello-python`);
        return;
      }
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setDemoBusy(false);
    }
  }

  async function createBlank() {
    const id = blankId.trim();
    if (!id) {
      setError("Project ID is required");
      return;
    }
    setBlankBusy(true);
    try {
      const created = await projectsApi.create({
        project_id: id,
        goal: blankGoal.trim(),
        agents: [],
      });
      setBlankOpen(false);
      setBlankId("");
      setBlankGoal("");
      navigate(`/projects/${created.project_id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBlankBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Projects</h1>
        <div className="dropdown" ref={menuRef}>
          <button className="primary" onClick={() => navigate("/propose")}>
            + New project
          </button>
          <button
            className="primary dropdown-toggle"
            aria-label="More options"
            onClick={() => setMenuOpen((v) => !v)}
          >
            ▾
          </button>
          {menuOpen && (
            <ul className="dropdown-menu">
              <li
                onClick={() => {
                  setMenuOpen(false);
                  navigate("/propose");
                }}
              >
                Propose with goal
              </li>
              <li
                onClick={() => {
                  setMenuOpen(false);
                  setBlankOpen(true);
                }}
              >
                Create blank project
              </li>
            </ul>
          )}
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {loading ? (
        <p className="muted">loading…</p>
      ) : projects.length === 0 ? (
        <OnboardingPanel onTryDemo={() => void createDemo()} busy={demoBusy} />
      ) : (
        <table className="list">
          <thead>
            <tr>
              <th>Project</th>
              <th>Goal</th>
              <th>Agents</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {projects.map((p) => (
              <tr key={p.project_id}>
                <td>
                  <Link to={`/projects/${p.project_id}`} className="mono">
                    {p.project_id}
                  </Link>
                </td>
                <td>{p.goal}</td>
                <td className="small">
                  {(p.agents ?? []).map((a) => a.role).join(", ") || "—"}
                </td>
                <td>
                  <div className="row" style={{ justifyContent: "flex-end" }}>
                    <Link to={`/projects/${p.project_id}`}>
                      <button>Open</button>
                    </Link>
                    <button
                      className="danger"
                      onClick={() => handleDelete(p.project_id)}
                    >
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {blankOpen && (
        <div className="modal-backdrop" onClick={() => setBlankOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Create blank project</h3>
            <p className="muted small">
              Skip the meta-agent proposal. You'll land in Studio with an
              empty agent list and can build the team yourself.
            </p>
            <label>Project ID</label>
            <input
              type="text"
              autoFocus
              placeholder="my-blank-project"
              value={blankId}
              onChange={(e) => setBlankId(e.target.value)}
            />
            <label style={{ marginTop: 8 }}>Goal (optional)</label>
            <textarea
              rows={3}
              placeholder="What is this project for?"
              value={blankGoal}
              onChange={(e) => setBlankGoal(e.target.value)}
            />
            <div className="row" style={{ marginTop: 12, gap: 8 }}>
              <button
                className="primary"
                onClick={() => void createBlank()}
                disabled={blankBusy || !blankId.trim()}
              >
                {blankBusy ? "Creating…" : "Create"}
              </button>
              <button onClick={() => setBlankOpen(false)} disabled={blankBusy}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


/**
 * Phase G.2 — first-timer's empty Projects view. Replaces the stark
 * "no projects yet" with a guided next step.
 */
function OnboardingPanel({
  onTryDemo,
  busy,
}: {
  onTryDemo: () => void;
  busy: boolean;
}) {
  return (
    <div className="card onboarding-card">
      <h2 style={{ marginTop: 0 }}>Welcome to AgentFlow</h2>
      <p className="muted">
        Two paths to your first run, depending on what you want:
      </p>

      <div className="grid-2 onboarding-options">
        <div className="onboarding-option">
          <h3>Try the demo</h3>
          <p className="small muted">
            One-click: a curated project (Add a `hello.py` module) with
            the seed personas (Mike + Alex) pre-staged. Watch a 2-agent
            run end-to-end before bringing your own work.
          </p>
          <button
            className="primary"
            onClick={onTryDemo}
            disabled={busy}
          >
            {busy ? "Setting up…" : "Try the demo"}
          </button>
        </div>

        <div className="onboarding-option">
          <h3>Describe your goal</h3>
          <p className="small muted">
            The meta-agent reads your goal (and any inputs you attach
            — PDFs, a git repo, a prior run) and proposes a tailored
            team. You approve before anything runs.
          </p>
          <Link to="/propose">
            <button className="primary">Propose a team</button>
          </Link>
        </div>
      </div>

      <p className="small muted" style={{ marginTop: 16 }}>
        Have personas already? Visit the{" "}
        <Link to="/roster">Roster</Link> to see them and chat directly,
        or browse the{" "}
        <a
          href="https://github.com/Erik-Iuhas/AgentFlow#readme"
          target="_blank"
          rel="noreferrer"
        >
          README
        </a>{" "}
        for the full architecture.
      </p>
    </div>
  );
}
