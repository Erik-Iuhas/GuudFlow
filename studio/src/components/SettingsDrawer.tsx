// Track 4 — Settings drawer. Right slide-in panel; first widget is Storage.
// Structured with a Section abstraction so future widgets (Connections,
// Models & Limits, Personas) can drop into the same layout.

import { useEffect, useState } from "react";
import { settingsApi, type SettingsState, type StorageRootValidation } from "../api";
import { Button } from "./ui/button";
import { Alert, Input, Label, Select } from "./ui/form-controls";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./ui/sheet";

interface SettingsDrawerProps {
  open: boolean;
  onClose: () => void;
}

export function SettingsDrawer({ open, onClose }: SettingsDrawerProps) {
  const [settings, setSettings] = useState<SettingsState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load settings when drawer opens.
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    settingsApi
      .get()
      .then(setSettings)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [open]);

  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-[420px] max-w-full overflow-y-auto" aria-label="Settings">
        <SheetHeader>
          <SheetTitle>Settings</SheetTitle>
          <SheetDescription className="sr-only">Storage and appearance settings.</SheetDescription>
        </SheetHeader>

        {loading && <p className="m-0 text-sm text-muted-foreground">Loading…</p>}
        {error && <Alert variant="error">Error: {error}</Alert>}

        {settings && (
          <div>
            {settings.env_overrides_active && <EnvOverrideBanner />}

            <StorageSection
              settings={settings}
              onUpdated={(next) => setSettings(next)}
            />
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

/** Warns that server environment variables override the saved storage settings. */
export function EnvOverrideBanner() {
  return (
    <Alert variant="warn" className="mb-4">
      Environment variables (e.g. <code className="rounded-sm bg-muted px-1">GUUDFLOW_STORAGE_MODE</code>) are
      overriding settings on this server. Saving will persist your
      choice but won't take effect until env vars are unset.
    </Alert>
  );
}

// ── Storage section ───────────────────────────────────────────────────────


interface StorageSectionProps {
  settings: SettingsState;
  onUpdated: (next: SettingsState) => void;
}

export function StorageSection({ settings, onUpdated }: StorageSectionProps) {
  const [mode, setMode] = useState<"global" | "in-directory">(
    settings.effective.storage_mode,
  );
  const [storageRoot, setStorageRoot] = useState(
    settings.effective.storage_mode === "in-directory"
      ? settings.effective.storage_root.replace(/[/\\]\.flow[/\\]?$/, "")
      : "",
  );
  const [validation, setValidation] = useState<StorageRootValidation | null>(null);
  const [validating, setValidating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [restartRequired, setRestartRequired] = useState(false);
  const [hotReloaded, setHotReloaded] = useState(false);

  const validate = async () => {
    if (!storageRoot.trim()) {
      setValidation({
        valid: false,
        exists: false,
        writable: false,
        reason: "Path is required for in-directory mode.",
      });
      return;
    }
    setValidating(true);
    setValidation(null);
    try {
      const result = await settingsApi.validateStorageRoot(storageRoot);
      setValidation(result);
    } catch (e) {
      setValidation({
        valid: false,
        exists: false,
        writable: false,
        reason: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setValidating(false);
    }
  };

  const save = async () => {
    setSaveError(null);
    if (mode === "in-directory" && !validation?.valid) {
      setSaveError("Validate the storage root before saving.");
      return;
    }
    setSaving(true);
    try {
      const updates: { storage_mode: "global" | "in-directory"; storage_root?: string } = {
        storage_mode: mode,
      };
      if (mode === "in-directory") {
        updates.storage_root = storageRoot;
      }
      const result = await settingsApi.update(updates);
      setRestartRequired(result.restart_required);
      setHotReloaded(result.hot_reloaded === true && !result.restart_required);
      // Re-fetch effective to surface what changed.
      const fresh = await settingsApi.get();
      onUpdated(fresh);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="border-b border-border py-4 last:border-b-0">
      <h3 className="m-0 mb-1 text-sm font-semibold">Storage</h3>
      <p className="mb-4 mt-0 text-xs leading-relaxed text-muted-foreground">
        Where projects, runs, and per-project agent desks live. Persona memory
        (episodes, patterns, reputation) always stays global — personas cross
        directories by design.
      </p>

      <div className="mb-4 flex flex-col gap-1.5">
        <Label htmlFor="storage-mode">Mode</Label>
        <Select
          id="storage-mode"
          value={mode}
          onChange={(e) => {
            setMode(e.target.value as "global" | "in-directory");
            setValidation(null);
          }}
        >
          <option value="global">Global (data/)</option>
          <option value="in-directory">In-directory (.flow/ at chosen path)</option>
        </Select>
      </div>

      {mode === "in-directory" && (
        <div className="mb-4 flex flex-col gap-1.5">
          <Label htmlFor="storage-root">Storage root</Label>
          <div className="flex gap-2">
            <Input
              id="storage-root"
              type="text"
              value={storageRoot}
              onChange={(e) => {
                setStorageRoot(e.target.value);
                setValidation(null);
              }}
              placeholder="/path/to/your/repo"
              className="flex-1"
            />
            <Button variant="outline" onClick={validate} disabled={validating}>
              {validating ? "…" : "Validate"}
            </Button>
          </div>
          <small className="text-xs text-muted-foreground">
            <code className="rounded-sm bg-muted px-1">.flow/</code> will be created inside this directory on the next run.
          </small>
          {validation && (
            <Alert variant={validation.valid ? "success" : "error"} className="mt-1 text-xs">
              {validation.valid
                ? `✓ Valid path, writable.`
                : `✗ ${validation.reason}`}
            </Alert>
          )}
        </div>
      )}

      {saveError && <Alert variant="error" className="mb-3">{saveError}</Alert>}
      {hotReloaded && (
        <Alert variant="success" className="mb-3">
          <strong>✓ Saved.</strong> New storage location is live — no
          restart needed.
        </Alert>
      )}
      {restartRequired && (
        <Alert variant="warn" className="mb-3">
          <strong>Saved — but a restart is required.</strong> A run is
          in flight, so the backend kept the old storage config to avoid
          corrupting it. Once the run finishes, restart{" "}
          <code className="rounded-sm bg-muted px-1">uvicorn</code> to pick up the new location.
        </Alert>
      )}
      {mode === "in-directory" && settings.effective.storage_mode !== "in-directory" && !restartRequired && (
        <Alert variant="warn" className="mt-2">
          <strong>Heads up:</strong> switching to in-directory mode means projects
          won't transfer automatically. You'll see an empty list at the new
          location until you create projects there or migrate manually.
        </Alert>
      )}

      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </section>
  );
}
