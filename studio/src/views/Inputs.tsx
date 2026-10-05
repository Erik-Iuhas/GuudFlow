import { useEffect, useMemo, useState } from "react";
import { ApiError, inputsApi, settingsApi, type SettingsState } from "../api";
import { PriorRunPicker } from "../components/PriorRunPicker";
import { SourceRepoField } from "../components/SourceRepoField";
import { UploadDropzone } from "../components/UploadDropzone";
import { Button } from "../components/ui/button";
import { Alert, Skeleton, Table, Td, Th } from "../components/ui/form-controls";
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
  const [settings, setSettings] = useState<SettingsState | null>(null);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Fetch effective settings once — used to derive `storageBase` so the
  // In-place sub-mode of Source repo can lock to the user's chosen folder
  // (no re-paste). Failures are non-fatal: SourceRepoField falls back to
  // the disabled-In-place state when storageMode is unknown.
  useEffect(() => {
    let alive = true;
    settingsApi
      .get()
      .then((s) => { if (alive) setSettings(s); })
      .catch(() => { /* noop */ });
    return () => { alive = false; };
  }, []);

  const storageMode = settings?.effective.storage_mode ?? "global";
  const storageBase = useMemo(() => {
    const root = settings?.effective.storage_root ?? "";
    return root.replace(/[/\\]\.flow[/\\]?$/, "");
  }, [settings]);

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

  async function handleImportLocalPathInPlace(body: {
    path: string;
  }): Promise<{ findings: SecretFinding[] | null; ok: boolean }> {
    const res = await inputsApi.importLocalPathInPlace(projectId, body);
    await load();
    return {
      findings: res.warnings.length > 0 ? res.warnings : null,
      ok: true,
    };
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
        {error ? (
          <Alert variant="error">{error}</Alert>
        ) : (
          <Skeleton className="h-24" aria-busy="true" />
        )}
      </div>
    );
  }

  const sectionTitle = "mb-1 mt-6 text-base font-semibold first:mt-0";

  return (
    <div>
      {error && <Alert variant="error" className="mb-3">{error}</Alert>}

      <h3 className={sectionTitle}>Uploads</h3>
      <p className="mb-2 mt-0 text-xs text-muted-foreground">
        Files attached here are visible to agents at{" "}
        <span className="font-mono">workspace/inputs/uploads/</span>.
      </p>
      <UploadDropzone onUpload={handleUpload} />
      {state.uploads.length === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">No uploads yet.</p>
      ) : (
        <div className="mt-2">
          <Table>
            <thead>
              <tr>
                <Th>Filename</Th>
                <Th>Size</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {state.uploads.map((u) => (
                <tr key={u.filename}>
                  <Td className="font-mono">{u.filename}</Td>
                  <Td className="text-xs text-muted-foreground">{humanSize(u.size)}</Td>
                  <Td>
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => void handleDeleteUpload(u.filename)}
                    >
                      Remove
                    </Button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}

      <h3 className={sectionTitle}>Source repo</h3>
      <p className="mb-2 mt-0 text-xs text-muted-foreground">
        Choose how agents see the code for this project. Git URL clones into a
        fresh <span className="font-mono">workspace/working/</span>; Local path
        either copies in (Isolated) or has agents work directly in your
        directory (In-place).
      </p>
      <SourceRepoField
        current={state.source_repo}
        storageMode={storageMode}
        storageBase={storageBase}
        onImportGit={handleImportGit}
        onImportLocalPath={handleImportLocalPath}
        onImportLocalPathInPlace={handleImportLocalPathInPlace}
        onDetach={handleDetachSourceRepo}
      />

      <h3 className={sectionTitle}>Prior runs</h3>
      <p className="mb-2 mt-0 text-xs text-muted-foreground">
        Attach a completed run's deliverables as input — useful for
        continuation runs that build on an earlier ship. Optional feedback
        becomes <span className="font-mono">feedback.md</span> for the next team.
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
