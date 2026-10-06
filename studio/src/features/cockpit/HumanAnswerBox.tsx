import { useState } from "react";
import { ApiError, runsApi } from "../../api";
import { Button } from "../../components/ui/button";
import { Textarea } from "../../components/ui/form-controls";

/**
 * Answer box inside the HUMAN_NEEDED banner. Sends the answer to the run, which clears the
 * pause and passes the answer to every role. The banner disappears when HUMAN_RESPONDED arrives.
 */
export function HumanAnswerBox({ runId, onSubmit }: {
  runId: string;
  /** Tests inject; defaults to runsApi.respond. */
  onSubmit?: (answer: string) => Promise<unknown>;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    const answer = text.trim();
    if (!answer || busy) return;
    setBusy(true);
    setError(null);
    try {
      await (onSubmit ? onSubmit(answer) : runsApi.respond(runId, answer));
      setText("");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 flex flex-col gap-2" data-testid="human-answer">
      <Textarea
        aria-label="Your answer"
        placeholder="Tell the team how to continue"
        rows={2}
        maxLength={4000}
        value={text}
        disabled={busy}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void send();
        }}
      />
      {error && <div role="alert" className="text-xs text-destructive">{error}</div>}
      <div>
        <Button size="sm" onClick={() => void send()} disabled={!text.trim() || busy}>
          {busy ? "Sending…" : "Send answer"}
        </Button>
      </div>
    </div>
  );
}
