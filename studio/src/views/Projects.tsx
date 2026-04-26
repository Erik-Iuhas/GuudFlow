import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, projectsApi } from "../api";
import type { ProjectConfig } from "../types";

export function Projects() {
  const [projects, setProjects] = useState<ProjectConfig[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    void load();
  }, []);

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
    // Optimistic update — remove locally, revert on failure.
    const prev = projects;
    setProjects(projects.filter((p) => p.project_id !== id));
    try {
      await projectsApi.remove(id);
    } catch (e) {
      setProjects(prev);
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <div className="page">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Projects</h1>
        <button className="primary" onClick={() => navigate("/propose")}>
          + New project
        </button>
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
    </div>
  );
}
