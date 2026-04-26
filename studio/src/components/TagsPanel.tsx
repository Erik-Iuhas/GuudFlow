import { useEffect, useState } from "react";
import { ApiError, runsApi } from "../api";
import type { RunTag } from "../api";

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
  if (error) return <div className="error small">tags: {error}</div>;
  if (tags.length === 0) return null;

  return (
    <div className="card tags-panel">
      <h3 style={{ marginTop: 0 }}>Review points ({tags.length})</h3>
      <p className="muted small">
        Git tags auto-created at every APPROVED merge. Each is a
        known-good tree state you can manually check out via{" "}
        <code>git checkout {tags[0]?.name}</code> in the run's{" "}
        <code>workspace/working/</code>.
      </p>
      <ul className="tags-list">
        {tags.map((t) => (
          <li key={t.name}>
            <strong className="mono">{t.name}</strong>{" "}
            <span className="muted small mono">{t.short_sha}</span>
            {t.message && (
              <span className="small" style={{ marginLeft: 8 }}>
                — {t.message}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
