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
  onDetach?: () => Promise<void>;
}

type Mode = "git" | "local";

export function SourceRepoField({
  current,
  onImportGit,
  onImportLocalPath,
  onDetach,
}: SourceRepoFieldProps) {
  const [editing, setEditing] = useState(current === null);
  const [mode, setMode] = useState<Mode>("git");
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
      const res =
        mode === "git"
          ? await onImportGit({
              git_url: gitUrl.trim(),
              branch: branch.trim() || "main",
              shallow,
              force,
            })
          : await onImportLocalPath({ path: localPath.trim(), force });
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
              {current.kind === "git_url" ? "git" : "local"}
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
          <label>Absolute path on the API host</label>
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
            The API process must have read access to this path. Multi-tenant
            deployments restrict this via{" "}
            <span className="mono">GUUDFLOW_LOCAL_PATH_ALLOWLIST</span>.
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
