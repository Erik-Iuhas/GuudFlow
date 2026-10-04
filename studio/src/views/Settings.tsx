// Settings page — top-nav-routed replacement for the gear → drawer pattern.
// Reuses StorageSection from the (now legacy) SettingsDrawer component.

import { useEffect, useState } from "react";
import { settingsApi, type SettingsState } from "../api";
import { StorageSection } from "../components/SettingsDrawer";
import { ThemeToggle } from "../components/ThemeToggle";

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

      <section className="mb-4 max-w-[720px]">
        <h2 className="mb-2 text-base font-semibold">Appearance</h2>
        <ThemeToggle />
        <p className="mt-2 text-xs text-muted-foreground">
          Dark mode currently restyles the new components only; legacy views follow in a later UI step.
        </p>
      </section>

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
