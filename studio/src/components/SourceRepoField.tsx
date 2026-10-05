import { useState } from "react";
import type { SecretFinding, SourceRepoMeta } from "../types";
import { checkCls, errorCls, fieldCls, labelCls, warnCls } from "../lib/field";
import { cn } from "../lib/utils";
import { FolderPicker } from "./FolderPicker";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

export interface SourceRepoFieldProps {
  current: SourceRepoMeta | null;
  /** Effective storage mode from /api/settings. When `"in-directory"` the
   *  In-place sub-mode is enabled; in `"global"` it's disabled with a hint. */
  storageMode?: "global" | "in-directory";
  /** User-chosen base directory (storage_root with trailing `.flow/`
   *  stripped). Drives the read-only chip in In-place mode. */
  storageBase?: string;
  onImportGit: (body: {
    git_url: string;
    branch?: string;
    shallow?: boolean;
    force?: boolean;
  }) => Promise<{ findings: SecretFinding[] | null; ok: boolean }>;
  onImportLocalPath: (body: {
    path: string;
    force?: boolean;
  }) => Promise<{ findings: SecretFinding[] | null; ok: boolean }>;
  onImportLocalPathInPlace: (body: {
    path: string;
  }) => Promise<{ findings: SecretFinding[] | null; ok: boolean }>;
  onDetach?: () => Promise<void>;
}

type Mode = "git" | "local";
type LocalSubMode = "isolated" | "in-place";

export function SourceRepoField({
  current,
  storageMode = "global",
  storageBase = "",
  onImportGit,
  onImportLocalPath,
  onImportLocalPathInPlace,
  onDetach,
}: SourceRepoFieldProps) {
  const inPlaceAvailable = storageMode === "in-directory" && storageBase.length > 0;
  const [editing, setEditing] = useState(current === null);
  const [mode, setMode] = useState<Mode>("git");
  const [localSubMode, setLocalSubMode] = useState<LocalSubMode>("isolated");
  const [gitUrl, setGitUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [shallow, setShallow] = useState(true);
  const [localPath, setLocalPath] = useState("");
  const [findings, setFindings] = useState<SecretFinding[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  async function submit(force: boolean) {
    setError(null);
    setFindings(null);
    setBusy(true);
    try {
      let res: { findings: SecretFinding[] | null; ok: boolean };
      if (mode === "git") {
        res = await onImportGit({
          git_url: gitUrl.trim(),
          branch: branch.trim() || "main",
          shallow,
          force,
        });
      } else if (localSubMode === "in-place") {
        // Path is locked to storageBase from Settings — single source of truth.
        res = await onImportLocalPathInPlace({ path: storageBase });
      } else {
        res = await onImportLocalPath({ path: localPath.trim(), force });
      }
      if (res.ok) {
        setEditing(false);
        setGitUrl("");
        setLocalPath("");
      } else if (res.findings) {
        setFindings(res.findings);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (current && !editing) {
    return (
      <Card className="mb-3 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <Badge variant="secondary" data-source-kind={current.kind}>
              {current.kind === "git_url"
                ? "git"
                : current.kind === "local_path_in_place"
                  ? "local · in-place"
                  : "local · copy"}
            </Badge>{" "}
            {current.kind === "git_url" ? (
              <span className="font-mono text-xs">
                {current.git_url} @ {current.branch}
              </span>
            ) : (
              <span className="font-mono text-xs">{current.path}</span>
            )}
          </div>
          <div className="flex items-center gap-3">
            <Button variant="outline" onClick={() => setEditing(true)}>Replace</Button>
            {onDetach && (
              <Button variant="destructive" onClick={() => void onDetach()}>
                Remove
              </Button>
            )}
          </div>
        </div>
        <p className="mb-0 mt-1 text-xs text-muted-foreground">
          Imported {current.imported_at?.replace("T", " ").split(".")[0]}
        </p>
      </Card>
    );
  }

  return (
    <Card className="mb-3 p-4">
      <div className="flex items-center gap-3">
        <label className="mb-0 flex cursor-pointer items-center gap-1.5 text-sm text-foreground">
          <input
            type="radio"
            className={checkCls}
            checked={mode === "git"}
            onChange={() => setMode("git")}
          />
          Git URL
        </label>
        <label className="mb-0 flex cursor-pointer items-center gap-1.5 text-sm text-foreground">
          <input
            type="radio"
            className={checkCls}
            checked={mode === "local"}
            onChange={() => setMode("local")}
          />
          Local path
        </label>
      </div>

      {mode === "git" ? (
        <div className="mt-2 grid grid-cols-2 gap-4">
          <div className="col-span-2">
            <label className={labelCls}>Git URL</label>
            <input
              type="text"
              className={fieldCls}
              placeholder="https://github.com/example/repo"
              value={gitUrl}
              onChange={(e) => setGitUrl(e.target.value)}
            />
          </div>
          <div>
            <label className={labelCls}>Branch</label>
            <input
              type="text"
              className={fieldCls}
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
            />
          </div>
          <div>
            <label className="mb-0 flex cursor-pointer items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <input
                type="checkbox"
                className={checkCls}
                checked={shallow}
                onChange={(e) => setShallow(e.target.checked)}
              />
              Shallow clone (--depth 1)
            </label>
          </div>
        </div>
      ) : (
        <div className="mt-2">
          <div className="grid grid-cols-2 gap-2">
            {[
              { value: "isolated" as const, title: "Isolated", hint: "Copy this directory into the run's workspace. Original is untouched.", available: true },
              { value: "in-place" as const, title: "In-place", hint: inPlaceAvailable
                ? "Agents work directly in your Storage root. Pair with PR mode = manual."
                : "Set Storage root in Settings to use In-place.", available: inPlaceAvailable },
            ].map((o) => {
              const selected = localSubMode === o.value;
              const disabled = !o.available;
              return (
                <button
                  key={o.value}
                  type="button"
                  onClick={() => { if (!disabled) setLocalSubMode(o.value); }}
                  disabled={disabled}
                  className={cn(
                    "flex flex-col gap-0.5 rounded-md border bg-card px-3 py-2.5 text-left text-card-foreground",
                    selected && !disabled
                      ? "border-status-complete bg-status-complete/10"
                      : "border-border",
                    disabled ? "cursor-not-allowed bg-muted opacity-65" : "cursor-pointer",
                  )}
                  data-testid={`source-local-submode-${o.value}`}
                >
                  <span className="text-[13px] font-semibold">
                    {selected && !disabled ? "● " : "○ "}{o.title}
                  </span>
                  <span className="text-xs leading-tight text-muted-foreground">
                    {o.hint}
                  </span>
                </button>
              );
            })}
          </div>

          {localSubMode === "in-place" && inPlaceAvailable ? (
            <div className="mt-3">
              <label className={labelCls}>Working directory</label>
              <div
                className="mt-1 flex items-center gap-2 rounded-md border border-border bg-muted px-2.5 py-2"
                data-testid="source-in-place-chip"
              >
                <span aria-hidden>📁</span>
                <span className="flex-1 overflow-x-auto font-mono text-xs">
                  {storageBase}
                </span>
                <span className="text-xs text-muted-foreground">From Settings → Storage root</span>
              </div>
              <p className="mb-0 mt-1.5 text-xs text-muted-foreground">
                ⚠ Agents will modify files in this directory directly. Strongly
                recommended: keep PR mode = manual on every project so main is
                never auto-merged.
              </p>
            </div>
          ) : (
            <div className="mt-3">
              <label className={labelCls}>
                Absolute path on the API host
              </label>
              <div className="flex items-center gap-1.5">
                <input
                  type="text"
                  className={cn(fieldCls, "flex-1")}
                  placeholder="/abs/path/to/dir"
                  value={localPath}
                  onChange={(e) => setLocalPath(e.target.value)}
                />
                <Button type="button" variant="outline" onClick={() => setPickerOpen(true)}>
                  Browse…
                </Button>
              </div>
              {pickerOpen && (
                <FolderPicker
                  initialPath={localPath || undefined}
                  onSelect={setLocalPath}
                  onClose={() => setPickerOpen(false)}
                />
              )}
              <p className="mb-0 mt-1 text-xs text-muted-foreground">
                The API process must have read access to this path. Multi-tenant
                deployments restrict this via{" "}
                <span className="font-mono">GUUDFLOW_LOCAL_PATH_ALLOWLIST</span>.
              </p>
            </div>
          )}
        </div>
      )}

      {findings && findings.length > 0 && (
        <div className={cn(warnCls, "mt-2")}>
          <p className="m-0 mb-1.5">
            <strong>Secrets detected.</strong> The following files look
            sensitive and were not imported:
          </p>
          <ul className="my-1.5 ml-5 p-0">
            {findings.map((f) => (
              <li key={f.path}>
                <span className="font-mono text-xs">{f.path}</span> — {f.reason}
              </li>
            ))}
          </ul>
          <p className="m-0 text-xs text-muted-foreground">
            "Import anyway" strips these files before the source lands.
          </p>
        </div>
      )}

      {error && <div className={cn(errorCls, "mb-0 mt-2")}>{error}</div>}

      <div className="mt-2 flex items-center gap-2">
        <Button
          onClick={() => void submit(false)}
          disabled={
            busy ||
            (mode === "git"
              ? !gitUrl.trim()
              : localSubMode === "in-place"
                ? !inPlaceAvailable
                : !localPath.trim())
          }
        >
          {busy ? "Importing…" : "Import"}
        </Button>
        {findings && findings.length > 0 && (
          <Button
            variant="outline"
            onClick={() => void submit(true)}
            disabled={busy}
          >
            Import anyway (strip secrets)
          </Button>
        )}
        {current && (
          <Button variant="outline" onClick={() => setEditing(false)} disabled={busy}>
            Cancel
          </Button>
        )}
      </div>
    </Card>
  );
}
