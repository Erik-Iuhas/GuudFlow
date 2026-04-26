import { useState } from "react";
import { ApiError, runsApi } from "../api";

/**
 * Phase G.3 — small button that re-enqueues a failed run via the
 * G-prime.2 endpoint. Used inline in run history lists. The
 * Run-view "Resume" button is its own thing because it lives next
 * to the Continue button + needs different styling.
 */
export function ResumeButton({
  runId,
  onResumed,
}: {
  runId: string;
  onResumed?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setError(null);
    try {
      await runsApi.resume(runId);
      onResumed?.();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          void go();
        }}
        disabled={busy}
        title={error ?? "Re-run from the persisted state"}
      >
        {busy ? "…" : "Resume"}
      </button>
    </>
  );
}
