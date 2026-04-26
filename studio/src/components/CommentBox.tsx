import { useState } from "react";
import { ApiError, runsApi } from "../api";

interface Props {
  runId: string;
  /** Optional override — defaults to runsApi.postComment. Tests inject. */
  onSubmit?: (text: string, priority: "normal" | "high") => Promise<void>;
}

export function CommentBox({ runId, onSubmit }: Props) {
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
    <div className="card">
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div className="col" style={{ flex: 1 }}>
          <label className="small">
            Post a comment to run <span className="mono">{runId}</span>
          </label>
          <input
            placeholder="Add some context for the agents — 'use Tailwind, not raw CSS'"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void send();
            }}
            disabled={busy}
          />
          {error && <div className="error" style={{ marginTop: 4 }}>{error}</div>}
        </div>
        <div className="col" style={{ minWidth: 100 }}>
          <label className="small">Priority</label>
          <select
            value={priority}
            onChange={(e) => setPriority(e.target.value as "normal" | "high")}
            disabled={busy}
          >
            <option value="normal">normal</option>
            <option value="high">high</option>
          </select>
        </div>
        <button
          className="primary"
          onClick={send}
          disabled={!text.trim() || busy}
          style={{ alignSelf: "flex-end" }}
        >
          {busy ? "Posting…" : "Post"}
        </button>
      </div>
    </div>
  );
}
