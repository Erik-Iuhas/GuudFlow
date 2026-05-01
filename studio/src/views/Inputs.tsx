import { useEffect, useState } from "react";
import { ApiError, inputsApi } from "../api";
import { PriorRunPicker } from "../components/PriorRunPicker";
import { SourceRepoField } from "../components/SourceRepoField";
import { UploadDropzone } from "../components/UploadDropzone";
import type { InputsState, SecretFinding } from "../types";

export interface InputsProps {
  projectId: string;
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function Inputs({ projectId }: InputsProps) {
  const [state, setState] = useState<InputsState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function load() {
    try {
      const s = await inputsApi.list(projectId);
      setState(s);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function handleUpload(files: File[]) {
    try {
      await inputsApi.uploadFiles(projectId, files);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function handleDeleteUpload(filename: string) {
    try {
      await inputsApi.deleteUpload(projectId, filename);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function handleImportGit(body: {
    git_url: string;
    branch?: string;
    shallow?: boolean;
    force?: boolean;
  }): Promise<{ findings: SecretFinding[] | null; ok: boolean }> {
    try {
      const res = await inputsApi.importGit(projectId, body);
      await load();
      return {
        findings: res.warnings.length > 0 ? res.warnings : null,
        ok: true,
      };
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        try {
          const parsed = JSON.parse(e.detail) as {
            findings?: SecretFinding[];
          };
          return { findings: parsed.findings ?? [], ok: false };
        } catch {
          /* fallthrough */
        }
      }
      throw e;
    }
  }

  async function handleImportLocalPath(body: {
    path: string;
    force?: boolean;
  }): Promise<{ findings: SecretFinding[] | null; ok: boolean }> {
    try {
      const res = await inputsApi.importLocalPath(projectId, body);
      await load();
      return {
        findings: res.warnings.length > 0 ? res.warnings : null,
        ok: true,
      };
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        try {
          const parsed = JSON.parse(e.detail) as {
            findings?: SecretFinding[];
          };
          return { findings: parsed.findings ?? [], ok: false };
        } catch {
          /* fallthrough */
        }
      }
      throw e;
    }
  }

  async function handleDetachSourceRepo() {
    try {
      await inputsApi.detachSourceRepo(projectId);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function handleAttachPriorRun(runId: string, feedback: string) {
    try {
      await inputsApi.attachPriorRun(projectId, runId, feedback || undefined);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function handleDetachPriorRun(runId: string) {
    try {
      await inputsApi.detachPriorRun(projectId, runId);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  if (!state) {
    return (
      <div>
        {error ? <div className="error">{error}</div> : <p className="muted">loading…</p>}
      </div>
    );
  }

  return (
    <div>
      {error && <div className="error">{error}</div>}

      <h3>Uploads</h3>
      <p className="muted small">
        Files attached here are visible to agents at{" "}
        <span className="mono">workspace/inputs/uploads/</span>.
      </p>
      <UploadDropzone onUpload={handleUpload} />
      {state.uploads.length === 0 ? (
        <p className="muted small" style={{ marginTop: 8 }}>
          No uploads yet.
        </p>
      ) : (
        <table className="list" style={{ marginTop: 8 }}>
          <thead>
            <tr>
              <th>Filename</th>
              <th>Size</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {state.uploads.map((u) => (
              <tr key={u.filename}>
                <td className="mono">{u.filename}</td>
                <td className="small muted">{humanSize(u.size)}</td>
                <td>
                  <button
                    className="danger small"
                    onClick={() => void handleDeleteUpload(u.filename)}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3 style={{ marginTop: 24 }}>Source repo</h3>
      <p className="muted small">
        A source tree seeds <span className="mono">workspace/working/</span>{" "}
        with a fresh git history. Choose git URL or local path; only one
        source is active at a time.
      </p>
      <SourceRepoField
        current={state.source_repo}
        onImportGit={handleImportGit}
        onImportLocalPath={handleImportLocalPath}
        onDetach={handleDetachSourceRepo}
      />

      <h3 style={{ marginTop: 24 }}>Prior runs</h3>
      <p className="muted small">
        Attach a completed run's deliverables as input — useful for
        continuation runs that build on an earlier ship. Optional feedback
        becomes <span className="mono">feedback.md</span> for the next team.
      </p>
      <PriorRunPicker
        projectId={projectId}
        attached={state.prior_runs}
        onAttach={handleAttachPriorRun}
        onDetach={handleDetachPriorRun}
      />
    </div>
  );
}
