import { useState } from "react";
import { messageWorker } from "../../api";
import { Button } from "../../components/ui/button";

/** Message one worker directly (worker context plan F3). "Send" puts it in the worker's next
 * brief; "Send and wake" also starts the worker again (a worker with no card gets one turn
 * whose task is the message; a running one goes again right after its current turn). */
export function MessageWorker({ runId, role, name, live }: {
  runId: string;
  role: string;
  name?: string;
  live: boolean;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  if (!live) return null;
  const who = name && name !== role ? name : role;
  async function send(wake: boolean) {
    const message = text.trim();
    if (!message) return;
    setBusy(true);
    setNote(null);
    try {
      await messageWorker(runId, role, message, wake);
      setText("");
      setNote(wake ? `Sent. ${who} picks it up on its next turn, starting now or right after this one.`
        : `Sent. ${who} reads it at the start of its next turn.`);
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="grid gap-1.5 border-b border-border px-3 py-2" data-testid={`message-${role}`}>
      <label className="grid gap-1 text-xs text-muted-foreground">
        Message {who}
        <textarea
          className="min-h-[2.5rem] rounded-md border border-input bg-background p-2 text-sm text-foreground"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void send(true); }}
          placeholder="More information, a correction, or a nudge to get going."
          aria-label={`Message ${who}`}
        />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy || !text.trim()} onClick={() => void send(true)}>Send and wake</Button>
        <Button size="sm" variant="outline" disabled={busy || !text.trim()} onClick={() => void send(false)}>Send</Button>
        {note && <span className="text-xs text-muted-foreground" role="status">{note}</span>}
      </div>
    </div>
  );
}
