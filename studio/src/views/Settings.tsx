// Settings page — top-nav-routed replacement for the gear → drawer pattern.
// Reuses StorageSection from the (now legacy) SettingsDrawer component.

import { useEffect, useState } from "react";
import { settingsApi, type SettingsState } from "../api";
import { StorageSection } from "../components/SettingsDrawer";

export function Settings() {
  const [settings, setSettings] = useState<SettingsState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    settingsApi
      .get()
      .then(setSettings)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="page">
      <h1>Settings</h1>

      {loading && <div className="settings-loading">Loading…</div>}
      {error && <div className="settings-error">Error: {error}</div>}

      {settings && (
        <div className="settings-body" style={{ maxWidth: 720 }}>
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
    </div>
  );
}
