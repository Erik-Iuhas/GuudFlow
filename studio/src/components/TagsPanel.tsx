import { useEffect, useState } from "react";
import { ApiError, runsApi } from "../api";
import type { RunTag } from "../api";
import { Card } from "./ui/card";
import { errorCls } from "../lib/field";

/**
 * Phase G.5 — surfaces git tags the runner created at APPROVED
 * merges. Each tag is a known-good review point. The actual
 * "rewind to here" action is deferred to a follow-up phase
 * (semantics need more design — see plan-g.md G.5 notes).
 */
export function TagsPanel({ runId }: { runId: string }) {
  const [tags, setTags] = useState<RunTag[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const t = await runsApi.tags(runId);
        if (!cancelled) {
          setTags(t);
          setLoaded(true);
        }
      } catch (e) {
        if (!cancelled) {
          // 404 — run has no git workspace yet. Silent; the panel
          // simply doesn't render anything.
          if (e instanceof ApiError && e.status === 404) {
            setLoaded(true);
            return;
          }
          setError(e instanceof ApiError ? e.detail : String(e));
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (!loaded) return null;
  if (error) return <div className={`${errorCls} text-xs`}>tags: {error}</div>;
  if (tags.length === 0) return null;

  return (
    <Card className="mb-3 border-status-blocked/30 bg-status-blocked/10 p-4">
      <h3 className="mb-2 mt-0 text-base font-semibold">Review points ({tags.length})</h3>
      <p className="my-2 text-xs text-muted-foreground">
        Git tags auto-created at every APPROVED merge. Each is a
        known-good tree state you can manually check out via{" "}
        <code className="font-mono">git checkout {tags[0]?.name}</code> in the run's{" "}
        <code className="font-mono">workspace/working/</code>.
      </p>
      <ul className="m-0 mt-2 list-none p-0">
        {tags.map((t) => (
          <li key={t.name} className="border-b border-status-blocked/20 px-2 py-1.5 text-[13px] last:border-b-0">
            <strong className="font-mono">{t.name}</strong>{" "}
            <span className="font-mono text-xs text-muted-foreground">{t.short_sha}</span>
            {t.message && (
              <span className="ml-2 text-xs">
                — {t.message}
              </span>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}
