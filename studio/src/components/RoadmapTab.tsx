import { useEffect, useState } from "react";
import { ApiError, projectsApi } from "../api";
import { errorCls, fieldCls } from "../lib/field";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

/**
 * Phase K.2 — project-level roadmap document. Free-form markdown
 * the user owns; the team reads it at run start as a new prompt
 * layer (Layer 3.8) so a project gains continuity across runs.
 *
 * Optional first-class — empty roadmap is a valid state, and the
 * empty-state copy below makes that explicit. Not every project
 * needs one.
 */
export function RoadmapTab({ projectId }: { projectId: string }) {
  const [content, setContent] = useState("");
  const [original, setOriginal] = useState("");
  const [exists, setExists] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<Date | null>(null);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await projectsApi.getRoadmap(projectId);
      setContent(r.content);
      setOriginal(r.content);
      setExists(r.exists);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const r = await projectsApi.updateRoadmap(projectId, content);
      setOriginal(r.content);
      setExists(r.exists);
      setSavedAt(new Date());
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSaving(false);
    }
  }

  function clear() {
    if (!confirm("Clear the roadmap? This removes the file; the team will run without project context.")) {
      return;
    }
    setContent("");
    void save();
  }

  const dirty = content !== original;

  if (loading) {
    return <p className="text-muted-foreground">Loading roadmap…</p>;
  }

  return (
    <div>
      <p className="my-2 max-w-[760px] text-xs text-muted-foreground">
        Free-form markdown the team reads at run start. Use it for
        the bigger picture: what we're building, what's done, what's
        next. Optional — small projects don't need one. Empty saves
        to "no roadmap" and the team runs without project context.
      </p>

      {error && <div className={errorCls}>{error}</div>}

      {!exists && content === "" && <RoadmapEmptyHint />}

      <textarea
        rows={20}
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder="## Goal&#10;What this project is trying to be.&#10;&#10;## Done&#10;- ...&#10;&#10;## Next&#10;- ..."
        className={`${fieldCls} font-mono text-[13px]`}
      />

      <div className="mt-3 flex items-center gap-2">
        <Button onClick={() => void save()} disabled={saving || !dirty}>
          {saving ? "Saving…" : dirty ? "Save" : "Saved"}
        </Button>
        {exists && (
          <Button variant="destructive" onClick={clear} disabled={saving}>
            Clear roadmap
          </Button>
        )}
        {savedAt && !dirty && (
          <span className="text-xs text-muted-foreground">
            Saved {savedAt.toLocaleTimeString()}
          </span>
        )}
        <span className="ml-auto text-xs text-muted-foreground">
          {exists
            ? `Threaded into every run as Layer 3.8`
            : `Empty roadmap — layer skipped at runtime`}
        </span>
      </div>
    </div>
  );
}


function RoadmapEmptyHint() {
  return (
    <Card className="mb-3 border-status-blocked/30 bg-status-blocked/10 p-4">
      <strong>No roadmap yet</strong>
      <p className="m-0 mt-1 text-xs">
        Suggested shape — copy this in if it helps:
      </p>
      <pre className="mb-0 mt-2 whitespace-pre-wrap rounded-md border border-border bg-card px-3 py-2 font-mono text-xs">
{`## Goal
What this project is trying to be in one paragraph.

## Done
- Stuff that landed in past runs.

## Next
- The thing the next run should attempt.

## Notes / decisions
- Architectural calls the team should remember.`}
      </pre>
    </Card>
  );
}
