import { useState, type Ref } from "react";
import { ApiError, messageWorker, runsApi } from "../api";
import { errorCls, fieldCls } from "../lib/field";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

interface Props {
  runId: string;
  /** Optional override — defaults to runsApi.postComment. Tests inject. */
  onSubmit?: (text: string, priority: "normal" | "high") => Promise<void>;
  /** Lets a parent focus the input (cockpit `c` shortcut, HUMAN_NEEDED). */
  inputRef?: Ref<HTMLInputElement>;
  /** The run's workers: with them a "To" picker sends to one worker instead of everyone. */
  roles?: readonly string[];
  /** Worker names by role, for the picker. */
  names?: Record<string, string>;
  /** Called once the server took it, with the signal it created (for receipts). */
  onSent?: (sent: { ref: string; text: string; to: string | null; at: number }) => void;
}

export function CommentBox({ runId, onSubmit, inputRef, roles = [], names = {}, onSent }: Props) {
  const [text, setText] = useState("");
  const [to, setTo] = useState("");
  const [priority, setPriority] = useState<"normal" | "high">("normal");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send() {
    const trimmed = text.trim();
    if (!trimmed) return;
    // Optimistic: clear the textarea immediately. The signal will surface
    // on the live board via the WebSocket, so the user sees their own
    // comment appear there as confirmation.
    setText("");
    setError(null);
    setBusy(true);
    try {
      if (onSubmit) {
        await onSubmit(trimmed, priority);
      } else {
        const at = Date.now();
        // One worker: it reads it in its next turn (a priority note addressed to it).
        const res = to ? await messageWorker(runId, to, trimmed, false)
          : await runsApi.postComment(runId, trimmed, priority);
        if (res?.signal_id) onSent?.({ ref: res.signal_id, text: trimmed, to: to || null, at });
      }
    } catch (e) {
      // Revert optimistic clear so the user can retry.
      setText(trimmed);
      if (e instanceof ApiError && e.status === 410) {
        setError("This run is no longer active — comments can't land.");
      } else if (e instanceof ApiError) {
        setError(e.detail);
      } else {
        setError(String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="mb-3 p-4">
      <div className="flex items-start gap-3">
        <div className="flex flex-1 flex-col gap-2">
          <label className="mb-0 block text-xs font-medium text-muted-foreground">
            {to ? <>Message to <strong>{names[to] ?? to}</strong> only</> : <>Comment to every worker in run <span className="font-mono">{runId}</span></>}
          </label>
          <input
            ref={inputRef}
            aria-label="Comment for the agents"
            placeholder="Add some context for the agents — 'use Tailwind, not raw CSS'"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void send();
            }}
            disabled={busy}
            className={fieldCls}
          />
          {error && <div className={`${errorCls} mb-0 mt-1`}>{error}</div>}
        </div>
        {roles.length > 0 && (
          <div className="flex min-w-[120px] flex-col gap-2">
            <label className="mb-0 block text-xs font-medium text-muted-foreground" htmlFor={`to-${runId}`}>To</label>
            <select id={`to-${runId}`} aria-label="To" value={to} onChange={(e) => setTo(e.target.value)}
              disabled={busy} className={fieldCls}>
              <option value="">Everyone</option>
              {roles.map((r) => <option key={r} value={r}>{names[r] ? `${names[r]} (${r})` : r}</option>)}
            </select>
          </div>
        )}
        <div className="flex min-w-[100px] flex-col gap-2">
          <label className="mb-0 block text-xs font-medium text-muted-foreground">Priority</label>
          <select
            value={priority}
            onChange={(e) => setPriority(e.target.value as "normal" | "high")}
            disabled={busy}
            className={fieldCls}
          >
            <option value="normal">normal</option>
            <option value="high">high</option>
          </select>
        </div>
        <Button className="self-end" onClick={send} disabled={!text.trim() || busy}>
          {busy ? "Sending…" : "Post"}
        </Button>
      </div>
    </Card>
  );
}
