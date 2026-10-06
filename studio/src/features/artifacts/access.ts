import { useEffect, useState } from "react";
import { getToken } from "../../api";

// `<img>` / `<iframe>` cannot send the bearer token, so the run's artifact files are
// read with a short-lived cookie that `POST /api/runs/{id}/artifact-access` sets
// (HttpOnly, scoped to that run's artifact-files path). Renewed well before its hour runs out.
const RENEW_MS = 45 * 60 * 1000;

async function grant(runId: string, token: string): Promise<void> {
  await fetch(`/api/runs/${encodeURIComponent(runId)}/artifact-access`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    credentials: "same-origin",
  });
}

/**
 * True once the run's artifact files can load. Without a stored token there is
 * nothing to trade (dev mode), so it is ready at once. A failed grant still
 * resolves: the images then show their own broken state instead of waiting forever.
 */
export function useArtifactAccess(runId: string): boolean {
  const [ready, setReady] = useState(() => getToken() === null);
  useEffect(() => {
    const token = getToken();
    if (token === null) {
      setReady(true);
      return;
    }
    let alive = true;
    const run = () =>
      grant(runId, token)
        .catch(() => undefined)
        .finally(() => {
          if (alive) setReady(true);
        });
    void run();
    const timer = setInterval(() => void run(), RENEW_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [runId]);
  return ready;
}
