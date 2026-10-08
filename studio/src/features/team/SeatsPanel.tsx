import { useState } from "react";
import { ApiError, projectsApi } from "../../api";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Alert } from "../../components/ui/form-controls";
import { Segmented } from "../../components/ui/segmented";
import type { ProjectConfig, ProjectSeats } from "../../types";
import { usePmName } from "../pm/api";

const REVIEWER_OPTIONS: { value: ProjectSeats["reviewer"]; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "all", label: "Every item" },
  { value: "must", label: "Must requirements only" },
];

/** Who holds which seat (project flow plan §2). Edits save the project's `seats`. */
export function SeatsPanel({
  project, onSaved,
}: {
  project: ProjectConfig;
  /** Called with the saved project. */
  onSaved: (project: ProjectConfig) => void;
}) {
  const seats: ProjectSeats = project.seats ?? { lead: false, reviewer: "off" };
  const pmName = usePmName();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(next: ProjectSeats) {
    setSaving(true);
    setError(null);
    try {
      const updated = { ...project, seats: next };
      await projectsApi.update(project.project_id, updated);
      onSaved(updated);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className="mb-3 p-4" data-testid="seats-panel">
      <h2 className="m-0 mb-2 text-base font-semibold">Seats</h2>
      {error && <Alert variant="error" className="mb-2">{error}</Alert>}
      <dl className="m-0 grid grid-cols-[110px_1fr] items-center gap-x-3 gap-y-2 text-sm">
        <dt className="font-medium">Owner</dt>
        <dd className="m-0">You</dd>
        <dt className="font-medium">Lead</dt>
        <dd className="m-0 flex flex-wrap items-center gap-2">
          {seats.lead ? (
            <>
              <span>You</span>
              <Button size="sm" variant="outline" disabled={saving} onClick={() => void save({ ...seats, lead: false })}>
                Give it up
              </Button>
            </>
          ) : (
            <>
              <span className="text-muted-foreground">
                {(project.agents ?? []).some((a) => a.contract === "tech_lead")
                  ? "Empty: the tech lead approves its own design and records why"
                  : `Empty: ${pmName} decides and records why`}
              </span>
              <Button size="sm" variant="outline" disabled={saving} onClick={() => void save({ ...seats, lead: true })}>
                Take it
              </Button>
            </>
          )}
        </dd>
        <dt className="font-medium">Reviewer</dt>
        <dd className="m-0">
          <Segmented
            label="Reviewer seat"
            value={seats.reviewer}
            options={REVIEWER_OPTIONS}
            onChange={(reviewer) => void save({ ...seats, reviewer })}
          />
        </dd>
      </dl>
    </Card>
  );
}
