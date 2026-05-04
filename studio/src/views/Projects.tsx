import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, exampleProjectsApi, projectsApi, runsApi } from "../api";
import type { ExampleProjectSummary, ProjectConfig, RunMeta } from "../types";

export function Projects() {
  const [projects, setProjects] = useState<ProjectConfig[]>([]);
  const [activeRuns, setActiveRuns] = useState<RunMeta[]>([]);
  const [examples, setExamples] = useState<ExampleProjectSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [blankOpen, setBlankOpen] = useState(false);
  const [blankId, setBlankId] = useState("");
  const [blankGoal, setBlankGoal] = useState("");
  const [blankBusy, setBlankBusy] = useState(false);
  const [demoBusy, setDemoBusy] = useState(false);
  const [openTemplate, setOpenTemplate] =
    useState<ExampleProjectSummary | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    void load();
    void loadActiveRuns();
    void loadExamples();
    const timer = setInterval(() => void loadActiveRuns(), 5000);
    return () => clearInterval(timer);
  }, []);

  async function loadExamples() {
    try {
      setExamples(await exampleProjectsApi.list());
    } catch {
      // Non-fatal — gallery just won't render.
    }
  }

  async function handleTemplateInstantiated(
    project: ProjectConfig,
  ): Promise<void> {
    setProjects((prev) =>
      prev.find((p) => p.project_id === project.project_id)
        ? prev
        : [...prev, project],
    );
    setOpenTemplate(null);
    navigate(`/projects/${project.project_id}`);
  }

  async function loadActiveRuns() {
    try {
      const runs = await runsApi.list();
      setActiveRuns(runs.filter((r) => r.status === "running" || r.status === "queued"));
    } catch {
      /* non-fatal */
    }
  }

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
            declared_local_tools: ["fs-read", "fs-list", "fs-write"],
            persona_id: "mike-frontend-v1",
            model_tier: "fast",
          },
          {
            role: "reviewer",
            description: "Reviews the developer's work",
            depends_on: ["developer"],
            declared_local_tools: ["fs-read", "fs-list", "git-diff", "git-log"],
            persona_id: "alex-code_reviewer-v1",
            model_tier: "fast",
            agent_class_role_type: "code_reviewer",
            is_reviewer: true,
            max_tool_iterations: 5,
          },
        ],
        rbac: {
          developer: { allowed_scopes: ["fs-write"] },
          reviewer: { allowed_scopes: [] },
        },
        max_iterations: 3,
        // Disable approval gates on the demo so first-timer's
        // "Try demo → Run now" goes directly to a run without the
        // cost-preview interstitial. Real projects keep gates on.
        approval_gates: {
          pre_run_cost_preview: false,
          heavy_tier_mid_run_dispatch: false,
          iteration_breach_at: 0,
          per_iteration: false,
          token_budget_usd: null,
        },
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

      {activeRuns.length > 0 && (
        <div className="card" style={{ marginBottom: 16, padding: "12px 16px" }}>
          <div className="row" style={{ gap: 8, marginBottom: 8 }}>
            <span
              style={{
                display: "inline-block",
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: "#16a34a",
                animation: "pulse 1.5s infinite",
              }}
            />
            <strong className="small">Active runs</strong>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {activeRuns.map((r) => (
              <Link
                key={r.run_id}
                to={`/runs/${r.run_id}`}
                style={{ textDecoration: "none" }}
              >
                <div
                  className="row"
                  style={{
                    padding: "8px 12px",
                    background: "#f0fdf4",
                    border: "1px solid #bbf7d0",
                    borderRadius: 6,
                    gap: 12,
                  }}
                >
                  <span className={`tag tag-status-${r.status}`}>{r.status}</span>
                  <span className="mono small" style={{ flex: 1, color: "#1e40af" }}>
                    {r.run_id}
                  </span>
                  <span className="small muted">{r.project_id}</span>
                  <span className="small muted">
                    {r.created_at ? new Date(r.created_at).toLocaleTimeString() : ""}
                  </span>
                  <span className="small" style={{ color: "#6b7280" }}>Watch →</span>
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}

      {loading ? (
        <p className="muted">loading…</p>
      ) : projects.length === 0 ? (
        <>
          <OnboardingPanel onTryDemo={() => void createDemo()} busy={demoBusy} />
          {examples.length > 0 && (
            <ExampleProjectGallery
              examples={examples}
              onPick={setOpenTemplate}
              heading="Or start from a template"
              subheading="Six example projects covering the platform's headline patterns. Each ships with a pre-designed agent team — you just edit the goal."
            />
          )}
        </>
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

      {!loading && projects.length > 0 && examples.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <ExampleProjectGallery
            examples={examples}
            onPick={setOpenTemplate}
            heading="Start from a template"
            subheading="One-click pre-designed agent teams. Edit the goal and run."
          />
        </div>
      )}

      {openTemplate && (
        <ExampleTemplateModal
          template={openTemplate}
          onClose={() => setOpenTemplate(null)}
          onCreated={handleTemplateInstantiated}
          existingIds={new Set(projects.map((p) => p.project_id))}
        />
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
      <h2 style={{ marginTop: 0 }}>Welcome to GuudFlow</h2>
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
          href="https://github.com/Erik-Iuhas/GuudFlow#readme"
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


/**
 * One-click example project gallery. Each card pops the
 * ExampleTemplateModal so the user can edit the goal before
 * instantiating.
 */
function ExampleProjectGallery({
  examples,
  onPick,
  heading,
  subheading,
}: {
  examples: ExampleProjectSummary[];
  onPick: (t: ExampleProjectSummary) => void;
  heading: string;
  subheading?: string;
}) {
  return (
    <section className="example-gallery" style={{ marginTop: 16 }}>
      <h2 style={{ marginBottom: 4 }}>{heading}</h2>
      {subheading && (
        <p className="muted small" style={{ marginTop: 0, marginBottom: 12 }}>
          {subheading}
        </p>
      )}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
          gap: 12,
        }}
      >
        {examples.map((t) => (
          <button
            key={t.id}
            className="card example-card"
            onClick={() => onPick(t)}
            style={{
              textAlign: "left",
              cursor: "pointer",
              background: "white",
              padding: 16,
              border: "1px solid #e2e8f0",
              borderRadius: 8,
              display: "flex",
              flexDirection: "column",
              gap: 6,
              fontFamily: "inherit",
              fontSize: "inherit",
              transition: "border-color 0.15s, box-shadow 0.15s",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 22 }}>{t.icon}</span>
              <strong style={{ flex: 1 }}>{t.title}</strong>
              <span className="tag" style={{ fontSize: "0.75em" }}>
                {t.category}
              </span>
            </div>
            <p
              className="small"
              style={{ margin: "4px 0 0", color: "#475569" }}
            >
              {t.summary}
            </p>
            {t.requires.source_repo && (
              <p
                className="small"
                style={{
                  margin: "4px 0 0",
                  color: "#9333ea",
                  fontWeight: 500,
                }}
              >
                Requires source repo
              </p>
            )}
          </button>
        ))}
      </div>
    </section>
  );
}


/**
 * Modal shown after picking a template. Lets the user override the
 * project_id (auto-suggests free slug from the default + a counter)
 * and edit the goal before submitting. On success, the parent
 * navigates to the new project.
 */
function ExampleTemplateModal({
  template,
  onClose,
  onCreated,
  existingIds,
}: {
  template: ExampleProjectSummary;
  onClose: () => void;
  onCreated: (project: ProjectConfig) => void;
  existingIds: Set<string>;
}) {
  const initialId = (() => {
    if (!existingIds.has(template.default_project_id))
      return template.default_project_id;
    for (let i = 2; i < 100; i++) {
      const candidate = `${template.default_project_id}-${i}`;
      if (!existingIds.has(candidate)) return candidate;
    }
    return template.default_project_id;
  })();

  const [projectId, setProjectId] = useState(initialId);
  const [goal, setGoal] = useState(template.default_goal);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const res = await exampleProjectsApi.instantiate(template.id, {
        project_id: projectId.trim() || undefined,
        goal: goal.trim() || undefined,
      });
      onCreated(res.project);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 640 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 28 }}>{template.icon}</span>
          <div style={{ flex: 1 }}>
            <h3 style={{ margin: 0 }}>{template.title}</h3>
            <p className="small muted" style={{ margin: 0 }}>
              {template.category} · {template.summary}
            </p>
          </div>
        </div>

        {template.details && (
          <p className="small" style={{ marginTop: 12, whiteSpace: "pre-wrap" }}>
            {template.details}
          </p>
        )}

        {template.requires.hints.length > 0 && (
          <ul
            className="small muted"
            style={{ marginTop: 8, paddingLeft: 18 }}
          >
            {template.requires.hints.map((h, i) => (
              <li key={i}>{h}</li>
            ))}
          </ul>
        )}

        {template.requires.source_repo && (
          <p
            className="small"
            style={{
              marginTop: 8,
              color: "#9333ea",
              background: "#faf5ff",
              border: "1px solid #e9d5ff",
              padding: "6px 10px",
              borderRadius: 4,
            }}
          >
            This template expects a source repo. After creating the
            project, attach one via Inputs → Git URL or Local path
            before running.
          </p>
        )}

        {error && <div className="error">{error}</div>}

        <label style={{ marginTop: 12 }}>Project ID</label>
        <input
          type="text"
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
          disabled={busy}
        />

        <label style={{ marginTop: 8 }}>Goal</label>
        <textarea
          rows={8}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          disabled={busy}
          style={{ fontFamily: "inherit" }}
        />

        <div className="row" style={{ marginTop: 12, gap: 8 }}>
          <button
            className="primary"
            onClick={() => void submit()}
            disabled={busy || !projectId.trim() || !goal.trim()}
          >
            {busy ? "Creating…" : "Create project"}
          </button>
          <button onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
