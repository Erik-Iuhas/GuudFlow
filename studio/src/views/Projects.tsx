import { useEffect, useState } from "react";
import { useConfirm } from "../components/ConfirmProvider";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, exampleProjectsApi, projectsApi, runsApi } from "../api";
import type { ExampleProjectSummary, ProjectConfig, RunMeta } from "../types";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "../components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../components/ui/dropdown-menu";
import { Alert, Input, Label, Page, SectionTitle, Table, Td, Textarea, Th, linkClass } from "../components/ui/form-controls";
import { runStatusVariant } from "../lib/field";
import { cn } from "../lib/utils";

export function Projects() {
  const confirm = useConfirm();
  const [projects, setProjects] = useState<ProjectConfig[]>([]);
  const [activeRuns, setActiveRuns] = useState<RunMeta[]>([]);
  const [examples, setExamples] = useState<ExampleProjectSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [blankOpen, setBlankOpen] = useState(false);
  const [blankId, setBlankId] = useState("");
  const [blankGoal, setBlankGoal] = useState("");
  const [blankBusy, setBlankBusy] = useState(false);
  const [demoBusy, setDemoBusy] = useState(false);
  const [openTemplate, setOpenTemplate] =
    useState<ExampleProjectSummary | null>(null);
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
    if (!(await confirm({ title: `Delete project ${id}?`, confirmLabel: "Delete", destructive: true }))) return;
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
    <Page
      title="Projects"
      actions={
        <div className="flex">
          <Button className="rounded-r-none" onClick={() => navigate("/projects/new")}>
            + New project
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                className="rounded-l-none border-l border-l-primary-foreground/25 px-2"
                aria-label="More options"
              >
                ▾
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => navigate("/projects/new")}>
                Describe a new project
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setBlankOpen(true)}>
                Create blank project
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      }
    >
      {error && <Alert variant="error" className="mb-3">{error}</Alert>}

      {activeRuns.length > 0 && (
        <Card className="mb-4 px-4 py-3">
          <div className="mb-2 flex items-center gap-2">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-status-complete" />
            <strong className="text-xs">Active runs</strong>
          </div>
          <div className="flex flex-col gap-1.5">
            {activeRuns.map((r) => (
              <Link
                key={r.run_id}
                to={`/runs/${r.run_id}`}
                className="flex items-center gap-3 rounded-md border border-status-complete/30 bg-status-complete/10 px-3 py-2 text-foreground no-underline hover:no-underline"
              >
                <Badge variant={runStatusVariant(r.status)}>{r.status}</Badge>
                <span className="flex-1 font-mono text-xs text-status-running">{r.run_id}</span>
                <span className="text-xs text-muted-foreground">{r.project_id}</span>
                <span className="text-xs text-muted-foreground">
                  {r.created_at ? new Date(r.created_at).toLocaleTimeString() : ""}
                </span>
                <span className="text-xs text-muted-foreground">Watch →</span>
              </Link>
            ))}
          </div>
        </Card>
      )}

      {loading ? (
        <p className="text-muted-foreground">loading…</p>
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
        <Table>
          <thead>
            <tr>
              <Th>Project</Th>
              <Th>Goal</Th>
              <Th>Agents</Th>
              <Th></Th>
            </tr>
          </thead>
          <tbody>
            {projects.map((p) => (
              <tr key={p.project_id}>
                <Td>
                  <Link to={`/projects/${p.project_id}`} className={cn(linkClass, "font-mono")}>
                    {p.project_id}
                  </Link>
                </Td>
                <Td className="max-w-xl"><GoalText goal={p.goal} /></Td>
                <Td className="text-xs">
                  {(p.agents ?? []).map((a) => a.role).join(", ") || "—"}
                </Td>
                <Td>
                  <div className="flex items-center justify-end gap-3">
                    <Button asChild variant="outline">
                      <Link to={`/projects/${p.project_id}`}>Open</Link>
                    </Button>
                    <Button
                      variant="outline"
                      className="border-destructive/40 text-destructive hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive"
                      onClick={() => handleDelete(p.project_id)}
                    >
                      Delete
                    </Button>
                  </div>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      {!loading && projects.length > 0 && examples.length > 0 && (
        <div className="mt-6">
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
        <Dialog open onOpenChange={(o) => !o && setBlankOpen(false)}>
          <DialogContent>
            <DialogTitle>Create blank project</DialogTitle>
            <DialogDescription>
              Skip the meta-agent proposal. You'll land in Studio with an
              empty agent list and can build the team yourself.
            </DialogDescription>
            <div>
              <Label htmlFor="blank-id">Project ID</Label>
              <Input
                id="blank-id"
                type="text"
                autoFocus
                placeholder="my-blank-project"
                value={blankId}
                onChange={(e) => setBlankId(e.target.value)}
              />
              <Label htmlFor="blank-goal" className="mt-2">Goal (optional)</Label>
              <Textarea
                id="blank-goal"
                rows={3}
                placeholder="What is this project for?"
                value={blankGoal}
                onChange={(e) => setBlankGoal(e.target.value)}
              />
            </div>
            <DialogFooter className="justify-start">
              <Button onClick={() => void createBlank()} disabled={blankBusy || !blankId.trim()}>
                {blankBusy ? "Creating…" : "Create"}
              </Button>
              <Button variant="outline" onClick={() => setBlankOpen(false)} disabled={blankBusy}>
                Cancel
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </Page>
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
    <Card className="mb-3 border-primary/30 bg-primary/5 p-6">
      <h2 className="m-0 text-base font-semibold">Welcome to GuudFlow</h2>
      <p className="my-2 text-muted-foreground">
        Two paths to your first run, depending on what you want:
      </p>

      <div className="mt-4 grid grid-cols-2 gap-4">
        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="m-0 mb-2 text-base font-semibold">Try the demo</h3>
          <p className="mb-2 mt-0 text-xs text-muted-foreground">
            One-click: a curated project (Add a `hello.py` module) with
            the seed personas (Mike + Alex) pre-staged. Watch a 2-agent
            run end-to-end before bringing your own work.
          </p>
          <Button onClick={onTryDemo} disabled={busy}>
            {busy ? "Setting up…" : "Try the demo"}
          </Button>
        </div>

        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="m-0 mb-2 text-base font-semibold">Describe your goal</h3>
          <p className="mb-2 mt-0 text-xs text-muted-foreground">
            The meta-agent reads your goal (and any inputs you attach
            — PDFs, a git repo, a prior run) and proposes a tailored
            team. You approve before anything runs.
          </p>
          <Button asChild>
            <Link to="/projects/new">Start a project</Link>
          </Button>
        </div>
      </div>

      <p className="mb-0 mt-4 text-xs text-muted-foreground">
        Have personas already? Visit the{" "}
        <Link className={linkClass} to="/roster">Roster</Link> to see them and chat directly,
        or browse the{" "}
        <a
          className={linkClass}
          href="https://github.com/Erik-Iuhas/GuudFlow#readme"
          target="_blank"
          rel="noreferrer"
        >
          README
        </a>{" "}
        for the full architecture.
      </p>
    </Card>
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
    <section className="mt-4">
      <SectionTitle className="mb-1 mt-0">{heading}</SectionTitle>
      {subheading && (
        <p className="mb-3 mt-0 text-xs text-muted-foreground">{subheading}</p>
      )}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-3">
        {examples.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => onPick(t)}
            className="flex cursor-pointer flex-col gap-1.5 rounded-lg border border-border bg-card p-4 text-left font-[inherit] text-[length:inherit] text-card-foreground transition-colors hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <div className="flex items-center gap-2">
              <span className="text-[22px]">{t.icon}</span>
              <strong className="flex-1">{t.title}</strong>
              <Badge variant="secondary" className="text-[0.75em]">
                {t.category}
              </Badge>
            </div>
            <p className="m-0 mt-1 text-xs text-muted-foreground">{t.summary}</p>
            {t.requires.source_repo && (
              <p className="m-0 mt-1 text-xs font-medium text-status-waiting">
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
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-[640px]">
        <div className="flex items-center gap-2.5">
          <span className="text-[28px]">{template.icon}</span>
          <div className="flex-1">
            <DialogTitle>{template.title}</DialogTitle>
            <DialogDescription className="text-xs">
              {template.category} · {template.summary}
            </DialogDescription>
          </div>
        </div>

        {template.details && (
          <p className="m-0 whitespace-pre-wrap text-xs">{template.details}</p>
        )}

        {template.requires.hints.length > 0 && (
          <ul className="m-0 pl-[18px] text-xs text-muted-foreground">
            {template.requires.hints.map((h, i) => (
              <li key={i}>{h}</li>
            ))}
          </ul>
        )}

        {template.requires.source_repo && (
          <p className="m-0 rounded border border-status-waiting/30 bg-status-waiting/10 px-2.5 py-1.5 text-xs text-status-waiting">
            This template expects a source repo. After creating the
            project, attach one via Inputs → Git URL or Local path
            before running.
          </p>
        )}

        {error && <Alert variant="error">{error}</Alert>}

        <div>
          <Label htmlFor="tpl-id">Project ID</Label>
          <Input
            id="tpl-id"
            type="text"
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            disabled={busy}
          />

          <Label htmlFor="tpl-goal" className="mt-2">Goal</Label>
          <Textarea
            id="tpl-goal"
            rows={8}
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            disabled={busy}
          />
        </div>

        <DialogFooter className="justify-start">
          <Button onClick={() => void submit()} disabled={busy || !projectId.trim() || !goal.trim()}>
            {busy ? "Creating…" : "Create project"}
          </Button>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}


/** A project's goal in two lines, with "more" to read the rest in place. */
function GoalText({ goal }: { goal: string }) {
  const [open, setOpen] = useState(false);
  const long = goal.length > 160;
  return (
    <div className="text-sm">
      <p className={cn("m-0", !open && "line-clamp-2")} title={open ? undefined : goal}>{goal}</p>
      {long && (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="mt-0.5 border-0 bg-transparent p-0 text-xs text-muted-foreground underline hover:text-foreground"
        >
          {open ? "less" : "more"}
        </button>
      )}
    </div>
  );
}
