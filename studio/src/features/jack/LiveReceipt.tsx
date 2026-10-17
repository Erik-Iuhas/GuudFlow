import { useMemo } from "react";
import { useRunSocket } from "../../hooks/useRunSocket";
import { createRunStore } from "../../stores/runEvents";
import { RunStoreProvider, useRunStore } from "../../stores/runStoreContext";
import { AnswerReceipt } from "../cockpit/AnswerReceipt";
import { ReceiptLine } from "../cockpit/ReceiptLine";

/**
 * The live state of something a dock button sent to a run (docs/RESPONSIVENESS_PLAN.md R1),
 * wherever the owner is: a private feed of that run's board while the receipt shows.
 */
export function LiveReceipt({ runId, kind, refId, role, at }: {
  runId: string;
  kind: "message" | "answer" | "stop";
  refId?: string;
  role?: string;
  at: number;
}) {
  const store = useMemo(() => createRunStore(runId), [runId]);
  useRunSocket(runId, store);
  return (
    <RunStoreProvider store={store}>
      {kind === "message" && refId && role ? (
        <ReceiptLine refId={refId} role={role} who={role} sentAt={at} />
      ) : kind === "answer" ? (
        <AnswerReceipt />
      ) : (
        <Stopping />
      )}
    </RunStoreProvider>
  );
}

function Stopping() {
  const finishing = useRunStore((s) => Object.values(s.agents).filter((a) => a.state === "running")
    .map((a) => s.names[a.role] ?? a.role).join(", "));
  return (
    <span role="status" className="text-[11px] text-muted-foreground" data-testid="dock-stopping">
      {finishing ? `Stopping: ${finishing} finishing the current turn` : "Stopped"}
    </span>
  );
}
