// Track 4 — Settings drawer. Right slide-in panel; first widget is Storage.
// Structured with a Section abstraction so future widgets (Connections,
// Models & Limits, Personas) can drop into the same layout.

import { useEffect, useState } from "react";
import { settingsApi, type SettingsState, type StorageRootValidation } from "../api";

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

  // ESC closes the drawer.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div className="settings-backdrop" onClick={onClose} />
      <aside className="settings-drawer" role="dialog" aria-label="Settings">
        <header className="settings-header">
          <h2>Settings</h2>
          <button
            className="settings-close"
            onClick={onClose}
            aria-label="Close settings"
          >
            ×
          </button>
        </header>

        {loading && <div className="settings-loading">Loading…</div>}
        {error && <div className="settings-error">Error: {error}</div>}

        {settings && (
          <div className="settings-body">
            {settings.env_overrides_active && (
              <div className="settings-banner settings-banner-warn">
                Environment variables (e.g. <code>GUUDFLOW_STORAGE_MODE</code>) are
                overriding settings on this server. Saving will persist your
                choice but won't take effect until env vars are unset.
              </div>
            )}

            <StorageSection
              settings={settings}
              onUpdated={(next) => setSettings(next)}
            />
          </div>
        )}
      </aside>
    </>
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
    <section className="settings-section">
      <h3>Storage</h3>
      <p className="settings-section-help">
        Where projects, runs, and per-project agent desks live. Persona memory
        (episodes, patterns, reputation) always stays global — personas cross
        directories by design.
      </p>

      <div className="settings-field">
        <label htmlFor="storage-mode">Mode</label>
        <select
          id="storage-mode"
          value={mode}
          onChange={(e) => {
            setMode(e.target.value as "global" | "in-directory");
            setValidation(null);
          }}
        >
          <option value="global">Global (data/)</option>
          <option value="in-directory">In-directory (.flow/ at chosen path)</option>
        </select>
      </div>

      {mode === "in-directory" && (
        <div className="settings-field">
          <label htmlFor="storage-root">Storage root</label>
          <div className="settings-field-row">
            <input
              id="storage-root"
              type="text"
              value={storageRoot}
              onChange={(e) => {
                setStorageRoot(e.target.value);
                setValidation(null);
              }}
              placeholder="/path/to/your/repo"
            />
            <button onClick={validate} disabled={validating}>
              {validating ? "…" : "Validate"}
            </button>
          </div>
          <small className="settings-hint">
            <code>.flow/</code> will be created inside this directory on the next run.
          </small>
          {validation && (
            <div
              className={
                validation.valid
                  ? "settings-validation settings-validation-ok"
                  : "settings-validation settings-validation-bad"
              }
            >
              {validation.valid
                ? `✓ Valid path, writable.`
                : `✗ ${validation.reason}`}
            </div>
          )}

        </div>
      )}

      {saveError && <div className="settings-error">{saveError}</div>}
      {hotReloaded && (
        <div
          className="settings-banner"
          style={{
            background: "#f0fdf4",
            border: "1px solid #16a34a",
            color: "#166534",
            padding: "8px 10px",
            borderRadius: 6,
          }}
        >
          <strong>✓ Saved.</strong> New storage location is live — no
          restart needed.
        </div>
      )}
      {restartRequired && (
        <div className="settings-banner settings-banner-warn">
          <strong>Saved — but a restart is required.</strong> A run is
          in flight, so the backend kept the old storage config to avoid
          corrupting it. Once the run finishes, restart{" "}
          <code>uvicorn</code> to pick up the new location.
        </div>
      )}
      {mode === "in-directory" && settings.effective.storage_mode !== "in-directory" && !restartRequired && (
        <div className="settings-banner settings-banner-warn" style={{marginTop: 8}}>
          <strong>Heads up:</strong> switching to in-directory mode means projects
          won't transfer automatically. You'll see an empty list at the new
          location until you create projects there or migrate manually.
        </div>
      )}

      <div className="settings-actions">
        <button className="settings-save" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </section>
  );
}
