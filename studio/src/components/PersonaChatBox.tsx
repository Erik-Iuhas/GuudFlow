import { useState } from "react";
import { ApiError, personasApi } from "../api";
import type { PersonaChatResponse } from "../api";

/**
 * Phase G.0 — read-only chat with a persona. Wraps F.4's
 * `POST /api/personas/{id}/chat`. The persona's reply is shown
 * with the recall summary so the user can see how much memory
 * was surfaced to answer.
 */
export function PersonaChatBox({ personaId }: { personaId: string }) {
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    const trimmed = message.trim();
    if (!trimmed) return;
    const userTurn: ChatTurn = { role: "user", text: trimmed };
    setHistory((h) => [...h, userTurn]);
    setMessage("");
    setLoading(true);
    setError(null);
    try {
      const reply = await personasApi.chat(personaId, { message: trimmed });
      setHistory((h) => [
        ...h,
        { role: "persona", text: reply.reply, recall: reply.recall },
      ]);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card chat-box">
      <h3 style={{ marginTop: 0 }}>Chat with this persona</h3>
      <p className="muted small">
        Read-only — chat does not mutate persona memory. Each reply
        shows what memory was surfaced.
      </p>

      <div className="chat-history">
        {history.length === 0 && (
          <p className="muted small">
            Ask about a past run, a pattern, or this persona's preferences.
          </p>
        )}
        {history.map((turn, i) => (
          <div
            key={i}
            className={`chat-turn chat-turn-${turn.role}`}
          >
            <div className="chat-role">
              {turn.role === "user" ? "You" : "Persona"}
            </div>
            <div className="chat-text">{turn.text}</div>
            {turn.recall && (
              <div className="chat-recall small muted">
                drew on {turn.recall.patterns_used} patterns ·{" "}
                {turn.recall.anti_patterns_used} anti-patterns ·{" "}
                {turn.recall.episodes_used} episodes
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="chat-turn chat-turn-loading muted small">
            Thinking…
          </div>
        )}
      </div>

      {error && <div className="error small">{error}</div>}

      <div className="chat-input-row">
        <input
          type="text"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          placeholder="Ask about a past run, a pattern, anything…"
          disabled={loading}
        />
        <button
          className="primary"
          disabled={!message.trim() || loading}
          onClick={() => void send()}
        >
          {loading ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}

interface ChatTurn {
  role: "user" | "persona";
  text: string;
  recall?: PersonaChatResponse["recall"];
}
