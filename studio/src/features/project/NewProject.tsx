import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, inputsApi, metaApi, projectsApi } from "../../api";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Alert, Input, Label, Page, Textarea } from "../../components/ui/form-controls";
import { cn } from "../../lib/utils";
import type { StartMode } from "../../types";
import {
  DEFAULT_CAP_USD,
  LEAD_HINT,
  REVIEWER_HINT,
  START_MODES,
  buildProject,
  slugify,
} from "./newProjectModel";

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.detail : e instanceof Error ? e.message : String(e);
}

/** A selectable card: a real button with `aria-pressed`. */
function ChoiceCard({
  title,
  hint,
  pressed,
  onClick,
  disabled,
  badge,
}: {
  title: string;
  hint: string;
  pressed: boolean;
  onClick?: () => void;
  disabled?: boolean;
  badge?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex w-full cursor-pointer flex-col gap-0.5 rounded-md border bg-card px-3 py-2.5 text-left text-card-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default",
        pressed ? "border-status-complete bg-status-complete/10" : "border-border hover:bg-accent/40",
      )}
    >
      <span className="flex items-center justify-between gap-2 text-[13px] font-semibold">
        <span>
          <span aria-hidden>{pressed ? "● " : "○ "}</span>
          {title}
        </span>
        {badge && <span className="text-[11px] font-normal text-muted-foreground">{badge}</span>}
      </span>
      <span className="text-xs leading-tight text-muted-foreground">{hint}</span>
    </button>
  );
}

/** `/projects/new`: describe the project, pick seats and a start mode, then start planning. */
export function NewProject() {
  const navigate = useNavigate();
  const [goal, setGoal] = useState("");
  const [lead, setLead] = useState(false);
  const [reviewer, setReviewer] = useState(false);
  const [startMode, setStartMode] = useState<StartMode>("spec_first");
  const [cap, setCap] = useState(String(DEFAULT_CAP_USD));
  const [name, setName] = useState("");
  const [nameEdited, setNameEdited] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [repoOpen, setRepoOpen] = useState(false);
  const [repoUrl, setRepoUrl] = useState("");
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const projectId = nameEdited ? name : slugify(goal);
  const capNumber = cap.trim() === "" ? null : Number(cap);
  const capInvalid = capNumber !== null && (!Number.isFinite(capNumber) || capNumber < 0);
  const busy = progress !== null;
  const canStart = goal.trim().length > 0 && projectId.length > 0 && !capInvalid && !busy;

  async function start() {
    if (!canStart) return;
    setError(null);
    try {
      // 1. The project manager's team, shaped by the start mode (can take a while).
      setProgress("Drafting the team. This can take a minute…");
      const proposal = await metaApi.propose(goal.trim(), { startMode });
      // 2. The project, with seats, mode and the spend cap.
      setProgress("Creating the project…");
      await projectsApi.create(
        buildProject({ projectId, goal: goal.trim(), lead, reviewer, startMode, capUsd: capNumber }, proposal),
      );
    } catch (e) {
      setError(errorText(e));
      setProgress(null);
      return;
    }
    // 3. Attachments are best effort: the project exists, so a failure here is a note, not a stop.
    const notes: string[] = [];
    if (files.length > 0) {
      setProgress("Attaching files…");
      try {
        await inputsApi.uploadFiles(projectId, files);
      } catch (e) {
        notes.push(`Files were not attached (${errorText(e)}). Add them on Plan, under Files.`);
      }
    }
    if (repoOpen && repoUrl.trim()) {
      setProgress("Importing the repo…");
      try {
        await inputsApi.importGit(projectId, { git_url: repoUrl.trim(), branch: "main", shallow: true });
      } catch (e) {
        notes.push(`The repo was not imported (${errorText(e)}). Add it on Plan, under Files.`);
      }
    }
    if (notes.length > 0) setError(notes.join(" "));
    navigate(`/projects/${encodeURIComponent(projectId)}?tab=plan`);
  }

  return (
    <Page title="New project">
      {error && (
        <Alert variant="error" className="mb-3">
          {error}
        </Alert>
      )}
      <form
        className="grid gap-4 lg:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          void start();
        }}
      >
        <div className="flex min-h-[28rem] flex-col gap-2">
          <Label htmlFor="np-goal" className="m-0 text-base font-semibold">
            What are you making?
          </Label>
          <Textarea
            id="np-goal"
            className="min-h-[18rem] flex-1 resize-none"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            disabled={busy}
            placeholder="e.g. A small web app where my book club can vote on the next read"
          />
          <p className="m-0 text-xs text-muted-foreground">
            Rough is fine. Planning is a conversation: the project manager drafts a spec from this, and you shape it
            before anything is built.
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileInput}
              type="file"
              multiple
              className="hidden"
              aria-label="Files to attach"
              onChange={(e) => {
                const picked = Array.from(e.target.files ?? []);
                if (picked.length > 0) setFiles((prev) => [...prev, ...picked]);
                e.target.value = "";
              }}
            />
            <Button type="button" variant="outline" disabled={busy} onClick={() => fileInput.current?.click()}>
              Attach files
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              aria-pressed={repoOpen}
              onClick={() => setRepoOpen((v) => !v)}
            >
              Use an existing repo
            </Button>
          </div>
          {files.length > 0 && (
            <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0 text-xs" aria-label="Attached files">
              {files.map((f, i) => (
                <li key={`${f.name}-${i}`} className="flex items-center gap-1 rounded-sm bg-muted px-1.5 py-0.5">
                  {f.name}
                  <button
                    type="button"
                    aria-label={`Remove ${f.name}`}
                    disabled={busy}
                    className="cursor-pointer border-0 bg-transparent px-0.5 text-muted-foreground hover:text-foreground"
                    onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
          {repoOpen && (
            <div>
              <Label htmlFor="np-repo">Git URL of the repo</Label>
              <Input
                id="np-repo"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                disabled={busy}
                placeholder="https://github.com/you/your-repo.git"
              />
            </div>
          )}
        </div>

        <div className="flex flex-col gap-4">
          <section aria-labelledby="np-seats" className="flex flex-col gap-2">
            <h2 id="np-seats" className="m-0 text-base font-semibold">
              Your seats
            </h2>
            <ChoiceCard title="Owner" hint="You set the goal and accept the work." pressed disabled badge="always on" />
            <ChoiceCard title="Lead" hint={LEAD_HINT} pressed={lead} onClick={() => setLead((v) => !v)} disabled={busy} />
            <ChoiceCard
              title="Reviewer"
              hint={REVIEWER_HINT}
              pressed={reviewer}
              onClick={() => setReviewer((v) => !v)}
              disabled={busy}
            />
          </section>

          <section aria-labelledby="np-mode" className="flex flex-col gap-2">
            <h2 id="np-mode" className="m-0 text-base font-semibold">
              How should it start?
            </h2>
            {START_MODES.map((m) => (
              <ChoiceCard
                key={m.value}
                title={m.title}
                hint={m.hint}
                pressed={startMode === m.value}
                onClick={() => setStartMode(m.value)}
                disabled={busy}
              />
            ))}
          </section>

          <Card className="flex flex-col gap-3 p-4">
            <div>
              <Label htmlFor="np-cap">Spend cap per session ($)</Label>
              <Input
                id="np-cap"
                type="number"
                min={0}
                step={0.5}
                value={cap}
                onChange={(e) => setCap(e.target.value)}
                disabled={busy}
                className="w-32"
                aria-invalid={capInvalid}
              />
            </div>
            <div>
              <Label htmlFor="np-name">Project name</Label>
              <Input
                id="np-name"
                value={projectId}
                onChange={(e) => {
                  setName(e.target.value);
                  setNameEdited(true);
                }}
                disabled={busy}
              />
            </div>
            <div className="flex items-center gap-3">
              <Button type="submit" disabled={!canStart}>
                {busy ? "Working…" : "Start planning"}
              </Button>
              {progress && (
                <span role="status" className="text-xs text-muted-foreground">
                  {progress}
                </span>
              )}
            </div>
          </Card>
        </div>
      </form>
    </Page>
  );
}
