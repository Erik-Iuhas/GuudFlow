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
        <div className="card">
          <p className="muted">
            No projects yet. <Link to="/propose">Propose one</Link> to get started.
          </p>
        </div>
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
