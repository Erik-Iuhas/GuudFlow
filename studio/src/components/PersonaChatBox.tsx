import { useState } from "react";
import { ApiError, personasApi } from "../api";
import type { PersonaChatResponse } from "../api";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { Alert, Input } from "./ui/form-controls";

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
    <Card className="mt-4 bg-muted p-4">
      <h3 className="m-0 mb-1 text-base font-semibold">Chat with this persona</h3>
      <p className="mb-2 mt-0 text-sm text-muted-foreground">
        Read-only — chat does not mutate persona memory. Each reply
        shows what memory was surfaced.
      </p>

      <div
        className="my-2 max-h-80 overflow-y-auto rounded-md border border-border bg-card p-2.5"
        role="log"
        aria-live="polite"
        aria-label="Chat history"
      >
        {history.length === 0 && (
          <p className="m-0 text-xs text-muted-foreground">
            Ask about a past run, a pattern, or this persona's preferences.
          </p>
        )}
        {history.map((turn, i) => (
          <div
            key={i}
            className={cn(
              "border-b border-border py-2 last:border-b-0",
              turn.role === "user" && "my-1 rounded-sm border-b-0 bg-accent px-2 py-1.5",
            )}
          >
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
              {turn.role === "user" ? "You" : "Persona"}
            </div>
            <div className="mt-0.5 whitespace-pre-wrap text-sm">{turn.text}</div>
            {turn.recall && (
              <div className="mt-1 text-[11px] text-muted-foreground">
                drew on {turn.recall.patterns_used} patterns ·{" "}
                {turn.recall.anti_patterns_used} anti-patterns ·{" "}
                {turn.recall.episodes_used} episodes
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="py-2 text-xs italic text-muted-foreground">Thinking…</div>
        )}
      </div>

      {error && <Alert variant="error" className="mb-2 text-xs">{error}</Alert>}

      <div className="flex gap-2">
        <Input
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
          aria-label="Message"
          disabled={loading}
          className="flex-1"
        />
        <Button disabled={!message.trim() || loading} onClick={() => void send()}>
          {loading ? "…" : "Send"}
        </Button>
      </div>
    </Card>
  );
}

interface ChatTurn {
  role: "user" | "persona";
  text: string;
  recall?: PersonaChatResponse["recall"];
}
