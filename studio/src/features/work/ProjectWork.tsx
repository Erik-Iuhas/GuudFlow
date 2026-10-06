import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Alert, SectionTitle } from "../../components/ui/form-controls";
import {
  WorkApiError,
  useAddItem,
  useBacklog,
  useDecideRun,
  useGroom,
  usePatchItem,
  useProjectRuns,
  useSyncRemote,
  workApi,
  workKeys,
  type Proposal,
} from "./api";
import { Button } from "../../components/ui/button";
import { Suggestions, WorkHeader } from "./WorkHeader";
import { BacklogList } from "./BacklogList";
import { groupBacklog, splitRuns, topOpen } from "./model";
import { ReviewList, type DecisionError } from "./ReviewList";
import { WorkNotice, WorkOverview } from "./WorkOverview";
import { WorkTechnical } from "./WorkTechnical";
import { useDetailLevel } from "../project/useDetailLevel";
import { planKeys } from "../plan/api";
import { specApi } from "../spec/api";
import type { ProjectSeats } from "../../types";
import { Link } from "react-router-dom";

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
  projectId, onStartRun, starting, seats,
}: {
  projectId: string;
  seats?: ProjectSeats;
  onStartRun: (request: RunRequest) => void;
  starting?: boolean;
}) {
  const backlog = useBacklog(projectId);
  const runs = useProjectRuns(projectId);
  const add = useAddItem(projectId);
  const patch = usePatchItem(projectId);
  const decide = useDecideRun(projectId);
  const groom = useGroom(projectId);
  const syncRemote = useSyncRemote(projectId);
  const client = useQueryClient();
  const [detail] = useDetailLevel(projectId);
  const [afterSession, setAfterSession] = useState(false);
  const [proposals, setProposals] = useState<Proposal[] | null>(null);

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
      onSuccess: () => {
        if (!accept) return;
        // The project manager drafts spec suggestions from the session; the answer shows up in Plan.
        setAfterSession(true);
        void specApi.afterSession(projectId, runId)
          .then(() => client.invalidateQueries({ queryKey: planKeys.plan(projectId) }))
          .catch(() => undefined);
      },
      onError: (e) => setDecisionError({
        runId,
        message: e.message,
        files: e instanceof WorkApiError ? e.files : [],
      }),
      onSettled: () => setBusyRun(null),
    });
  }

  const loadError = backlog.error ?? runs.error ?? add.error ?? patch.error ?? groom.error
    ?? syncRemote.error;

  function addProposal(p: Proposal) {
    add.mutate({ title: p.title, acceptance: p.acceptance, kind: p.kind, size: p.size });
    setProposals((list) => (list ?? []).filter((x) => x.title !== p.title));
  }

  const technicalBody = (
    <>
      <div>
        <SectionTitle className="mt-0">Waiting for your review</SectionTitle>
        <ReviewList
          projectId={projectId}
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
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <SectionTitle className="m-0">Choose items for the next session</SectionTitle>
          <Button size="sm" variant="outline" disabled={groom.isPending}
            onClick={() => groom.mutate(undefined, { onSuccess: (p) => setProposals(p) })}>
            {groom.isPending ? "Thinking…" : "Suggest items"}
          </Button>
        </div>
        {proposals && (
          <div className="mb-3">
            <Suggestions proposals={proposals} onAdd={addProposal} onDismiss={() => setProposals(null)} />
          </div>
        )}
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
    </>
  );

  function reviewPanel(runId: string) {
    return (
      <ReviewList
        projectId={projectId}
        pending={pending.filter((r) => r.run_id === runId)}
        decided={[]}
        changes={changes}
        onViewChanges={(id) => void viewChanges(id)}
        onDecide={onDecide}
        onContinue={(id) => onStartRun({ continue_run: id, backlog_items: chosen.length ? chosen : undefined })}
        busyRunId={busyRun}
        error={decisionError}
      />
    );
  }

  return (
    <div className="flex flex-col gap-6" data-testid="project-work">
      {loadError && <Alert variant="error">{loadError.message}</Alert>}
      {runs.data && (
        <WorkHeader
          remote={runs.data.remote}
          autopilot={runs.data.autopilot}
          lastSync={syncRemote.data ?? null}
          onSync={() => syncRemote.mutate()}
          syncing={syncRemote.isPending}
        />
      )}
      {afterSession && (
        <WorkNotice>
          The project manager is reviewing the session for spec changes. <Link to="?tab=plan">See Plan.</Link>
        </WorkNotice>
      )}
      {detail === "overview" ? (
        <WorkOverview projectId={projectId} items={items} pendingRuns={pending} renderReview={reviewPanel} />
      ) : (
        <WorkTechnical projectId={projectId} items={items} seats={seats} pendingRuns={pending}>
          {technicalBody}
        </WorkTechnical>
      )}
    </div>
  );
}
