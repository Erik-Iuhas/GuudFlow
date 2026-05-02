import { useState } from "react";
import type { SecretFinding, SourceRepoMeta } from "../types";
import { FolderPicker } from "./FolderPicker";

export interface SourceRepoFieldProps {
  current: SourceRepoMeta | null;
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
  onImportGit,
  onImportLocalPath,
  onImportLocalPathInPlace,
  onDetach,
}: SourceRepoFieldProps) {
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
        res = await onImportLocalPathInPlace({ path: localPath.trim() });
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
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <span className={`tag tag-source-${current.kind}`}>
              {current.kind === "git_url"
                ? "git"
                : current.kind === "local_path_in_place"
                  ? "local · in-place"
                  : "local · copy"}
            </span>{" "}
            {current.kind === "git_url" ? (
              <span className="mono small">
                {current.git_url} @ {current.branch}
              </span>
            ) : (
              <span className="mono small">{current.path}</span>
            )}
          </div>
          <div className="row">
            <button onClick={() => setEditing(true)}>Replace</button>
            {onDetach && (
              <button onClick={() => void onDetach()} className="danger">
                Remove
              </button>
            )}
          </div>
        </div>
        <p className="muted small" style={{ marginTop: 4 }}>
          Imported {current.imported_at?.replace("T", " ").split(".")[0]}
        </p>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="row">
        <label>
          <input
            type="radio"
            checked={mode === "git"}
            onChange={() => setMode("git")}
          />{" "}
          Git URL
        </label>
        <label>
          <input
            type="radio"
            checked={mode === "local"}
            onChange={() => setMode("local")}
          />{" "}
          Local path
        </label>
      </div>

      {mode === "git" ? (
        <div className="grid-2" style={{ marginTop: 8 }}>
          <div style={{ gridColumn: "1 / span 2" }}>
            <label>Git URL</label>
            <input
              type="text"
              placeholder="https://github.com/example/repo"
              value={gitUrl}
              onChange={(e) => setGitUrl(e.target.value)}
            />
          </div>
          <div>
            <label>Branch</label>
            <input
              type="text"
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
            />
          </div>
          <div>
            <label>
              <input
                type="checkbox"
                checked={shallow}
                onChange={(e) => setShallow(e.target.checked)}
              />{" "}
              Shallow clone (--depth 1)
            </label>
          </div>
        </div>
      ) : (
        <div style={{ marginTop: 8 }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {[
              { value: "isolated" as const, title: "Isolated", hint: "Copy this directory into the run's workspace. Original is untouched." },
              { value: "in-place" as const, title: "In-place", hint: "Agents work directly in this directory. Pair with PR mode = manual." },
            ].map((o) => {
              const selected = localSubMode === o.value;
              return (
                <button
                  key={o.value}
                  type="button"
                  onClick={() => setLocalSubMode(o.value)}
                  style={{
                    textAlign: "left",
                    padding: "10px 12px",
                    borderRadius: 6,
                    border: selected ? "1.5px solid #16a34a" : "1px solid #e2e8f0",
                    background: selected ? "#f0fdf4" : "#fff",
                    cursor: "pointer",
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                  }}
                >
                  <span style={{ fontWeight: 600, fontSize: 13 }}>
                    {selected ? "● " : "○ "}{o.title}
                  </span>
                  <span className="muted small" style={{ lineHeight: 1.3 }}>
                    {o.hint}
                  </span>
                </button>
              );
            })}
          </div>

          <label style={{ display: "block", marginTop: 12 }}>
            Absolute path on the API host
          </label>
          <div className="row" style={{ gap: 6 }}>
            <input
              type="text"
              placeholder="/abs/path/to/dir"
              value={localPath}
              onChange={(e) => setLocalPath(e.target.value)}
              style={{ flex: 1 }}
            />
            <button type="button" onClick={() => setPickerOpen(true)}>
              Browse…
            </button>
          </div>
          {pickerOpen && (
            <FolderPicker
              initialPath={localPath || undefined}
              onSelect={setLocalPath}
              onClose={() => setPickerOpen(false)}
            />
          )}
          <p className="muted small" style={{ marginTop: 4 }}>
            {localSubMode === "in-place" ? (
              <>
                ⚠ Agents will modify files in this directory directly. Strongly
                recommended: keep PR mode = manual on every project so main is
                never auto-merged.
              </>
            ) : (
              <>
                The API process must have read access to this path. Multi-tenant
                deployments restrict this via{" "}
                <span className="mono">GUUDFLOW_LOCAL_PATH_ALLOWLIST</span>.
              </>
            )}
          </p>
        </div>
      )}

      {findings && findings.length > 0 && (
        <div className="warn" style={{ marginTop: 8 }}>
          <p>
            <strong>Secrets detected.</strong> The following files look
            sensitive and were not imported:
          </p>
          <ul>
            {findings.map((f) => (
              <li key={f.path}>
                <span className="mono small">{f.path}</span> — {f.reason}
              </li>
            ))}
          </ul>
          <p className="muted small">
            "Import anyway" strips these files before the source lands.
          </p>
        </div>
      )}

      {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}

      <div className="row" style={{ marginTop: 8, gap: 8 }}>
        <button
          className="primary"
          onClick={() => void submit(false)}
          disabled={busy || (mode === "git" ? !gitUrl.trim() : !localPath.trim())}
        >
          {busy ? "Importing…" : "Import"}
        </button>
        {findings && findings.length > 0 && (
          <button
            onClick={() => void submit(true)}
            disabled={busy}
          >
            Import anyway (strip secrets)
          </button>
        )}
        {current && (
          <button onClick={() => setEditing(false)} disabled={busy}>
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}
