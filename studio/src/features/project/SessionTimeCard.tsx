import { Card } from "../../components/ui/card";
import { Input, Label, Select } from "../../components/ui/form-controls";
import type { ProjectConfig } from "../../types";

type Mode = NonNullable<ProjectConfig["time_limit"]>;

const HELP: Record<Mode, string> = {
  soft: "At the limit no new card starts; running cards and their reviews finish, then the session goes to review. The overseer checks for loops at 80% and at the limit. Time paused for you does not count.",
  hard: "The session stops at the limit, even mid-card. For CI or demos that must end on time.",
  none: "No time limit. The budget and the stuck detectors still guard the session.",
};

/** Project setting: how long a session may run and what happens at the limit (plan §11.6). */
export function SessionTimeCard({ project, onChange }: { project: ProjectConfig; onChange: (p: ProjectConfig) => void }) {
  const mode: Mode = project.time_limit ?? "soft";
  const minutes = project.max_run_minutes ?? 30;
  return (
    <Card className="mb-3 p-4" data-testid="session-time">
      <h2 className="m-0 text-base font-semibold">Session time</h2>
      <p className="my-2 text-xs text-muted-foreground">Saves with the project on the next click of Save.</p>
      <div className="mt-3 flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1">
          <Label htmlFor="time-limit-mode">At the time limit</Label>
          <Select id="time-limit-mode" value={mode} onChange={(e) => onChange({ ...project, time_limit: e.target.value as Mode })}>
            <option value="soft">Soft: finish running cards</option>
            <option value="hard">Hard: stop on time</option>
            <option value="none">No time limit</option>
          </Select>
        </div>
        {mode !== "none" && (
          <div className="flex flex-col gap-1">
            <Label htmlFor="time-limit-minutes">Minutes</Label>
            <Input id="time-limit-minutes" type="number" min={5} max={240} className="w-24" value={minutes}
              onChange={(e) => onChange({ ...project, max_run_minutes: Math.max(5, Math.min(240, Number(e.target.value) || 30)) })} />
          </div>
        )}
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{HELP[mode]}</p>
    </Card>
  );
}
