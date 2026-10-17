import { useState } from "react";
import { messageWorker } from "../../api";
import { Button } from "../../components/ui/button";
import { ReceiptLine } from "./ReceiptLine";

interface Sent { ref: string; text: string; wake: boolean; at: number }

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
  const [sent, setSent] = useState<Sent[]>([]);
  if (!live) return null;
  const who = name && name !== role ? name : role;
  async function send(wake: boolean) {
    const message = text.trim();
    if (!message) return;
    setBusy(true);
    setNote(null);
    // The box clears at once; the receipt below carries the message from here.
    setText("");
    try {
      const at = Date.now();
      const res = await messageWorker(runId, role, message, wake);
      if (res.signal_id) {
        const ref = res.signal_id;
        setSent((list) => [{ ref, text: message, wake, at }, ...list].slice(0, 3));
      } else {
        setNote(`Sent. ${who} reads it at the start of its next turn.`);
      }
    } catch (e) {
      setText(message);
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
      {sent.map((m) => (
        <ReceiptLine key={m.ref} refId={m.ref} role={role} who={who} wake={m.wake} sentAt={m.at} text={m.text} />
      ))}
    </div>
  );
}
