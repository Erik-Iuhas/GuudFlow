import { useState, type Ref } from "react";
import { ApiError, runsApi } from "../api";
import { errorCls, fieldCls } from "../lib/field";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

interface Props {
  runId: string;
  /** Optional override — defaults to runsApi.postComment. Tests inject. */
  onSubmit?: (text: string, priority: "normal" | "high") => Promise<void>;
  /** Lets a parent focus the input (cockpit `c` shortcut, HUMAN_NEEDED). */
  inputRef?: Ref<HTMLInputElement>;
}

export function CommentBox({ runId, onSubmit, inputRef }: Props) {
  const [text, setText] = useState("");
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
        await runsApi.postComment(runId, trimmed, priority);
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
            Post a comment to run <span className="font-mono">{runId}</span>
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
          {busy ? "Posting…" : "Post"}
        </Button>
      </div>
    </Card>
  );
}
