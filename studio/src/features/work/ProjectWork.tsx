import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Alert, SectionTitle } from "../../components/ui/form-controls";
import {
  WorkApiError,
  useAddItem,
  useBacklog,
  useDecideRun,
  usePatchItem,
  useProjectRuns,
  workApi,
  workKeys,
} from "./api";
import { BacklogList } from "./BacklogList";
import { groupBacklog, splitRuns, topOpen } from "./model";
import { ReviewList, type DecisionError } from "./ReviewList";

export interface RunRequest {
  backlog_items?: string[];
  continue_run?: string;
}

/**
 * The project as ongoing work (plan §13): runs waiting for review on top, the
 * backlog below. `onStartRun` starts a run with the given payload (the page owns
 * the cost-preview flow).
 */
export function ProjectWork({
  projectId, onStartRun, starting,
}: {
  projectId: string;
  onStartRun: (request: RunRequest) => void;
  starting?: boolean;
}) {
  const backlog = useBacklog(projectId);
  const runs = useProjectRuns(projectId);
  const add = useAddItem(projectId);
  const patch = usePatchItem(projectId);
  const decide = useDecideRun(projectId);
  const client = useQueryClient();

  const [selected, setSelected] = useState<string[] | null>(null);
  const [changes, setChanges] = useState<Record<string, string | undefined>>({});
  const [decisionError, setDecisionError] = useState<DecisionError | null>(null);
  const [busyRun, setBusyRun] = useState<string | null>(null);

  const items = useMemo(() => backlog.data ?? [], [backlog.data]);
  const open = groupBacklog(items).open;
  // Default selection: the top three open items, until the user picks.
  const chosen = (selected ?? topOpen(open)).filter((id) => open.some((i) => i.id === id));
  useEffect(() => setSelected(null), [projectId]);

  const { pending, decided } = splitRuns(runs.data?.runs ?? []);

  async function viewChanges(runId: string) {
    setChanges((c) => ({ ...c, [runId]: undefined }));
    try {
      const diff = await client.fetchQuery({
        queryKey: workKeys.changes(projectId, runId),
        queryFn: () => workApi.changes(projectId, runId),
        staleTime: 0,
      });
      setChanges((c) => ({ ...c, [runId]: diff }));
    } catch (e) {
      setChanges((c) => ({ ...c, [runId]: `Could not load the changes: ${String(e)}` }));
    }
  }

  function onDecide(runId: string, accept: boolean, note: string) {
    setDecisionError(null);
    setBusyRun(runId);
    decide.mutate({ runId, accept, note }, {
      onError: (e) => setDecisionError({
        runId,
        message: e.message,
        files: e instanceof WorkApiError ? e.files : [],
      }),
      onSettled: () => setBusyRun(null),
    });
  }

  const loadError = backlog.error ?? runs.error ?? add.error ?? patch.error;

  return (
    <div className="flex flex-col gap-6" data-testid="project-work">
      {loadError && <Alert variant="error">{loadError.message}</Alert>}
      <div>
        <SectionTitle className="mt-0">Waiting for your review</SectionTitle>
        <ReviewList
          pending={pending}
          decided={decided}
          changes={changes}
          onViewChanges={(id) => void viewChanges(id)}
          onDecide={onDecide}
          onContinue={(id) => onStartRun({ continue_run: id, backlog_items: chosen.length ? chosen : undefined })}
          busyRunId={busyRun}
          error={decisionError}
        />
      </div>
      <div>
        <SectionTitle className="mt-0">Backlog</SectionTitle>
        {backlog.isLoading ? (
          <p className="m-0 text-sm text-muted-foreground">Loading the backlog…</p>
        ) : (
          <BacklogList
            items={items}
            selected={chosen}
            onToggle={(id) => setSelected(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id])}
            onMove={(itemId, priority) => patch.mutate({ itemId, patch: { priority } })}
            onDrop={(itemId) => patch.mutate({ itemId, patch: { status: "dropped" } })}
            onReopen={(itemId) => patch.mutate({ itemId, patch: { status: "open" } })}
            onAdd={(item) => add.mutate(item)}
            onStart={() => onStartRun({ backlog_items: chosen })}
            adding={add.isPending}
            starting={starting}
          />
        )}
      </div>
    </div>
  );
}
