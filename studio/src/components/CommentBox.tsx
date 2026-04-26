import { useState } from "react";

interface Props {
  runId: string;
  onSubmit?: (text: string, priority: "normal" | "high") => Promise<void>;
}

export function CommentBox({ runId, onSubmit }: Props) {
  const [text, setText] = useState("");
  const [priority, setPriority] = useState<"normal" | "high">("normal");
  const [error, setError] = useState<string | null>(null);

  async function send() {
    if (!text.trim()) return;
    try {
      if (onSubmit) await onSubmit(text, priority);
      // No backend route yet to POST a USER_COMMENT for an active run —
      // this is a Stage 8 follow-on. For now, the textarea is wired and
      // the button is enabled so the UX shape exists.
      setText("");
      setError(null);
    } catch (e) {
      setError(String(e));
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
          />
          {error && <div className="error" style={{ marginTop: 4 }}>{error}</div>}
        </div>
        <div className="col" style={{ minWidth: 100 }}>
          <label className="small">Priority</label>
          <select
            value={priority}
            onChange={(e) => setPriority(e.target.value as "normal" | "high")}
          >
            <option value="normal">normal</option>
            <option value="high">high</option>
          </select>
        </div>
        <button
          className="primary"
          onClick={send}
          disabled={!text.trim()}
          style={{ alignSelf: "flex-end" }}
        >
          Post
        </button>
      </div>
    </div>
  );
}
