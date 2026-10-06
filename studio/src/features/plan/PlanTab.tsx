import { useState } from "react";
import { Alert, Skeleton } from "../../components/ui/form-controls";
import { Button } from "../../components/ui/button";
import {
  useApproveDraft,
  useDiscardDraft,
  usePlan,
  useSendMessage,
  useSpec,
  useStartPlan,
  useTickChange,
} from "./api";
import { useProject } from "../../api/queries";
import { Conversation } from "./Conversation";
import { DraftPanel } from "./DraftPanel";
import { SpecView } from "./SpecView";

type Panel = "draft" | "spec";

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** The Plan tab: the conversation with the project manager beside the draft or the approved spec. */
export function PlanTab({ projectId, onTeamChanged }: { projectId: string; onTeamChanged?: () => void }) {
  const plan = usePlan(projectId);
  const spec = useSpec(projectId);
  const start = useStartPlan(projectId);
  const send = useSendMessage(projectId);
  const tick = useTickChange(projectId);
  const approve = useApproveDraft(projectId);
  const discard = useDiscardDraft(projectId);
  const project = useProject(projectId);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [approvedVersion, setApprovedVersion] = useState<number | null>(null);

  if (plan.isLoading) return <Skeleton className="h-64 w-full" />;
  if (plan.error) return <Alert variant="error">{message(plan.error)}</Alert>;

  const draft = plan.data?.draft ?? null;
  const shown: Panel = draft ? (panel ?? "draft") : "spec";
  const pending = start.isPending || send.isPending;
  const panelError = tick.error ?? approve.error ?? discard.error;

  return (
    <div className="flex flex-col gap-4 min-[1000px]:flex-row min-[1000px]:items-start">
      <Conversation
        messages={plan.data?.messages ?? []}
        pending={pending}
        onStart={() => {
          setApprovedVersion(null);
          return start.mutateAsync();
        }}
        onSend={(text, mode) => {
          setApprovedVersion(null);
          return send.mutateAsync({ text, mode });
        }}
      />
      <aside
        aria-label="Spec and draft"
        className="w-full shrink-0 rounded-lg border border-border bg-card p-4 min-[1000px]:w-[560px]"
      >
        {draft && (
          <div role="group" aria-label="Show" className="mb-3 inline-flex gap-1">
            <Button size="sm" variant={shown === "draft" ? "default" : "outline"} aria-pressed={shown === "draft"} onClick={() => setPanel("draft")}>
              Draft
            </Button>
            <Button size="sm" variant={shown === "spec" ? "default" : "outline"} aria-pressed={shown === "spec"} onClick={() => setPanel("spec")}>
              Spec
            </Button>
          </div>
        )}
        {approvedVersion !== null && !draft && (
          <Alert variant="success" role="status" className="mb-3">
            Approved: spec v{approvedVersion}
          </Alert>
        )}
        {shown === "draft" && draft ? (
          <DraftPanel
            draft={draft}
            spec={spec.data}
            team={project.data?.agents}
            busy={tick.isPending || approve.isPending || discard.isPending}
            error={panelError ? message(panelError) : null}
            onTick={(cid, accepted) => tick.mutate({ cid, accepted })}
            onApprove={() =>
              approve.mutate(undefined, {
                onSuccess: (r) => {
                  setApprovedVersion(r.version);
                  if ((r.team_changes?.length ?? 0) > 0) onTeamChanged?.();
                  setPanel(null);
                },
              })
            }
            onDiscard={() => discard.mutate(undefined, { onSuccess: () => setPanel(null) })}
          />
        ) : spec.data ? (
          <SpecView spec={spec.data} />
        ) : spec.error ? (
          <Alert variant="error">{message(spec.error)}</Alert>
        ) : (
          <Skeleton className="h-40 w-full" />
        )}
      </aside>
    </div>
  );
}
