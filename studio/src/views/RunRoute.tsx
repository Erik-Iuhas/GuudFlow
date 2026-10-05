import { useRef } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useProject, useRunMeta } from "../api/queries";
import { Cockpit } from "../features/cockpit/Cockpit";
import { Run } from "./Run";

/**
 * `/runs/:runId`. Runs on the opencode harness (the default) open the cockpit. A run whose
 * project asks for the legacy harness, or any run opened with `?legacy=1`, keeps the old
 * view. The choice is made once per run so a late project fetch never swaps the view away.
 */
export function RunRoute() {
  const { runId } = useParams<{ runId: string }>();
  const [params] = useSearchParams();
  if (!runId) return null;
  if (params.get("legacy") === "1") return <Run />;
  return <HarnessSwitch key={runId} runId={runId} />;
}

function HarnessSwitch({ runId }: { runId: string }) {
  const meta = useRunMeta(runId);
  const project = useProject(meta.data?.project_id);
  const choice = useRef<"legacy" | "cockpit" | null>(null);

  if (choice.current === null) {
    if (meta.data?.harness === "legacy") choice.current = "legacy";
    else if (meta.isError) choice.current = "cockpit"; // a live run may not have written its meta yet
    else if (meta.data && meta.data.harness === "opencode") choice.current = "cockpit";
    else if (meta.data) {
      if (project.data) choice.current = project.data.harness === "legacy" ? "legacy" : "cockpit";
      else if (project.isError) choice.current = "cockpit";
    }
  }

  if (choice.current === "legacy") return <Run />;
  if (choice.current === "cockpit") return <Cockpit runId={runId} />;
  return (
    <p className="p-4 text-sm text-muted-foreground" role="status">
      Loading run…
    </p>
  );
}
