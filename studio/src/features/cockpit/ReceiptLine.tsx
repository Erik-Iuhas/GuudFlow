import { useRunStore } from "../../stores/runStoreContext";
import { clock, receiptFor, receiptText, useElapsed } from "./receipts";

/** The live state of one thing sent to `role` (docs/RESPONSIVENESS_PLAN.md R1). */
export function ReceiptLine({ refId, role, who, wake, sentAt, text }: {
  refId: string;
  role: string;
  who: string;
  wake?: boolean;
  /** Client time it was sent, for the "how long so far" clock. */
  sentAt: number;
  text?: string;
}) {
  const signals = useRunStore((s) => s.signals);
  const running = useRunStore((s) => s.agents[role]?.state === "running");
  const r = receiptFor(signals, refId, role, { wake, running });
  const done = r.stage === "answered";
  const seconds = useElapsed(sentAt, !done);
  const tone = r.stage === "got" || done ? "text-status-complete" : "text-muted-foreground";
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 text-xs" role="status" data-testid={`receipt-${refId}`} data-stage={r.stage}>
      {text && <span className="max-w-[16rem] truncate font-medium" title={text}>“{text}”</span>}
      <span className={tone}>
        {!done && r.stage !== "got" && <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current align-middle" aria-hidden />}
        {receiptText(r, who)}
      </span>
      {!done && <span className="tabular-nums text-muted-foreground">{clock(seconds)}</span>}
    </div>
  );
}
