import { Alert, Label, Select } from "../../components/ui/form-controls";
import { Card } from "../../components/ui/card";
import type { ProjectConfig, StartMode } from "../../types";
import { useKinds } from "../design/api";
import { START_MODES } from "../project/newProjectModel";

/**
 * The project's kind and start mode, changeable after the project exists (role contracts plan
 * §3, §8): an older project moves to a kind here, usually with start mode "adopt" so its next
 * session recovers the design first. Saved with the project's Save button. Also lists what
 * would stop the team from working (`team_warnings`), which a run refuses to start with.
 */
export function KindPanel({ project, onChange }: { project: ProjectConfig; onChange: (next: ProjectConfig) => void }) {
  const kinds = useKinds();
  const rows = kinds.data?.kinds ?? [];
  const kind = rows.find((k) => k.id === project.kind);
  const modes = kind ? START_MODES.filter((m) => kind.start_modes.includes(m.value)) : START_MODES;
  const warnings = project.team_warnings ?? [];
  return (
    <Card className="mb-3 p-4" data-testid="kind-panel">
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label htmlFor="project-kind">Project kind</Label>
          <Select id="project-kind" value={project.kind ?? ""}
            onChange={(e) => {
              const next = rows.find((k) => k.id === e.target.value);
              const mode = next && project.start_mode && !next.start_modes.includes(project.start_mode)
                ? (next.default_start_mode as StartMode) : project.start_mode;
              onChange({ ...project, kind: e.target.value || undefined, start_mode: mode });
            }}>
            <option value="">not set (software app)</option>
            {rows.map((k) => <option key={k.id} value={k.id}>{k.title}</option>)}
          </Select>
        </div>
        <div>
          <Label htmlFor="project-start-mode">Start mode</Label>
          <Select id="project-start-mode" value={project.start_mode ?? ""}
            onChange={(e) => onChange({ ...project, start_mode: (e.target.value || undefined) as StartMode | undefined })}>
            <option value="">not set</option>
            {modes.map((m) => <option key={m.value} value={m.value}>{m.title}</option>)}
          </Select>
        </div>
      </div>
      <p className="m-0 mt-2 text-xs text-muted-foreground">
        {modes.find((m) => m.value === project.start_mode)?.hint
          ?? "For a project that already has code and no recorded design, pick “Adopt existing code”."}
        {" "}To plan with a tech lead, change the existing planner's role type and contract (gear on its card)
        rather than adding a second planner. Save to apply.
      </p>
      {warnings.length > 0 && (
        <Alert variant="error" className="mt-3" data-testid="team-warnings">
          <strong>A run will not start until the team is fixed:</strong>
          <ul className="m-0 mt-1 pl-4">{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </Alert>
      )}
    </Card>
  );
}
