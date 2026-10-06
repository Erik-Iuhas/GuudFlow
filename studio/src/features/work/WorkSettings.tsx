import { Card } from "../../components/ui/card";
import { Input, Label, Select } from "../../components/ui/form-controls";
import { Switch } from "../../components/ui/switch";
import type { ApprovalGates, AutopilotSettings, ProjectRemote } from "../../types";

export const DEFAULT_AUTOPILOT: AutopilotSettings = {
  enabled: false, every_hours: 24, take: 2, max_usd_per_run: 5, max_usd_per_week: 20, auto_accept: false,
};

type WorkGates = Pick<ApprovalGates, "check_command" | "project_remote" | "autopilot">;

/**
 * Settings for the project as ongoing work (plan §14): the check run before review,
 * the real remote (sync and publish) and the autopilot. Saved with the project.
 */
export function WorkSettings({
  gates, onChange,
}: {
  gates: Partial<ApprovalGates>;
  onChange: (patch: Partial<WorkGates>) => void;
}) {
  const remote: ProjectRemote | null = gates.project_remote ?? null;
  const pilot: AutopilotSettings = { ...DEFAULT_AUTOPILOT, ...(gates.autopilot ?? {}) };
  const setRemote = (patch: Partial<ProjectRemote>) => {
    const next = { url: "", branch: "main", publish: "none" as const, ...(remote ?? {}), ...patch };
    onChange({ project_remote: next.url.trim() ? next : null });
  };
  const setPilot = (patch: Partial<AutopilotSettings>) => onChange({ autopilot: { ...pilot, ...patch } });

  return (
    <Card className="mb-3 p-4" data-testid="work-settings">
      <h2 className="m-0 text-base font-semibold">Project work</h2>
      <p className="my-2 text-xs text-muted-foreground">
        How runs check their code, where accepted work goes, and whether runs start on their own.
      </p>

      <div className="mt-3 grid gap-4">
        <div>
          <Label htmlFor="check-command">Check before review</Label>
          <Input id="check-command" placeholder="e.g. python3 -m unittest discover -s tests -q" value={gates.check_command ?? ""}
            onChange={(e) => onChange({ check_command: e.target.value })} />
          <p className="m-0 mt-1 text-xs text-muted-foreground">
            Runs on each finished run's code in a sandbox with no network. Its result shows in the review queue.
          </p>
        </div>

        <fieldset className="m-0 grid gap-2 border-0 p-0">
          <legend className="mb-1 text-sm font-medium">Remote repository</legend>
          <div className="grid gap-2 sm:grid-cols-[1fr_10rem]">
            <div>
              <Label htmlFor="remote-url">URL</Label>
              <Input id="remote-url" placeholder="https://github.com/acme/app.git" value={remote?.url ?? ""}
                onChange={(e) => setRemote({ url: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="remote-branch">Branch</Label>
              <Input id="remote-branch" value={remote?.branch ?? "main"} disabled={!remote}
                onChange={(e) => setRemote({ branch: e.target.value })} />
            </div>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <Label htmlFor="remote-publish">When you accept a run</Label>
              <Select id="remote-publish" value={remote?.publish ?? "none"} disabled={!remote}
                onChange={(e) => setRemote({ publish: e.target.value as ProjectRemote["publish"] })}>
                <option value="none">Keep it in GuudFlow</option>
                <option value="branch">Push a guudflow/run branch</option>
                <option value="pr">Open a pull request (GitHub)</option>
                <option value="main">Push to the branch</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="remote-token">Token variable</Label>
              <Input id="remote-token" placeholder="GUUDFLOW_GIT_TOKEN" value={remote?.token_env ?? ""}
                disabled={!remote} onChange={(e) => setRemote({ token_env: e.target.value || null })} />
            </div>
          </div>
          <p className="m-0 text-xs text-muted-foreground">
            The remote is synced into the project before every run. The token stays in an environment
            variable on the server (its name must start with GUUDFLOW_GIT_); agents never see it.
          </p>
        </fieldset>

        <fieldset className="m-0 grid gap-2 border-0 p-0">
          <legend className="mb-1 text-sm font-medium">Autopilot</legend>
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={pilot.enabled} onCheckedChange={(v) => setPilot({ enabled: v })}
              aria-label="Autopilot enabled" />
            Start runs on their own, taking the top backlog items
          </label>
          <div className="grid gap-2 sm:grid-cols-4">
            <NumberField id="pilot-every" label="Every (hours)" value={pilot.every_hours} step={0.5}
              onChange={(v) => setPilot({ every_hours: v })} />
            <NumberField id="pilot-take" label="Items per run" value={pilot.take} step={1}
              onChange={(v) => setPilot({ take: v })} />
            <NumberField id="pilot-run" label="Max $ per run" value={pilot.max_usd_per_run} step={0.5}
              onChange={(v) => setPilot({ max_usd_per_run: v })} />
            <NumberField id="pilot-week" label="Max $ per week" value={pilot.max_usd_per_week} step={1}
              onChange={(v) => setPilot({ max_usd_per_week: v })} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={pilot.auto_accept} onCheckedChange={(v) => setPilot({ auto_accept: v })}
              aria-label="Auto-accept" />
            Accept a run by itself when every item finished and the check passed
          </label>
          {pilot.auto_accept && !(gates.check_command ?? "").trim() && (
            <p className="m-0 text-xs text-status-blocked">
              Auto-accept needs a check before review; without one every run waits for you.
            </p>
          )}
        </fieldset>
      </div>
    </Card>
  );
}

function NumberField({
  id, label, value, step, onChange,
}: { id: string; label: string; value: number; step: number; onChange: (v: number) => void }) {
  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="number" min={0} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}
