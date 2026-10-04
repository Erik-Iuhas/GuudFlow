import { getToken } from "../../api";

// The three write paths the gallery uses. Kept local so this folder does not touch api.ts,
// which the cockpit and board work also edit.

async function postJson(path: string, body: unknown): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(path, { method: "POST", headers, body: JSON.stringify(body) });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      /* keep the status text */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
}

const run = (runId: string): string => `/api/runs/${encodeURIComponent(runId)}`;

/** `POST /api/runs/{id}/cards/{task_id}/comments`: lands on the card's next brief. */
export function postCardComment(runId: string, taskId: string, text: string): Promise<void> {
  return postJson(`${run(runId)}/cards/${encodeURIComponent(taskId)}/comments`, { text });
}

/** `POST /api/runs/{id}/visual_feedback`: answers an agent's `request_visual_feedback`. */
export function postVisualFeedback(runId: string, requestId: string, feedback: string): Promise<void> {
  return postJson(`${run(runId)}/visual_feedback`, { request_id: requestId, feedback });
}

/** `POST /api/runs/{id}/comment`: the ambient run comment, for artifacts with no card. */
export function postRunComment(runId: string, text: string): Promise<void> {
  return postJson(`${run(runId)}/comment`, { text, priority: "normal" });
}
