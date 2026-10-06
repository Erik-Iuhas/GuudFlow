// Settings page — top-nav-routed replacement for the gear → drawer pattern.
// Reuses StorageSection from the (now legacy) SettingsDrawer component.

import { useEffect, useState } from "react";
import { settingsApi, type SettingsState } from "../api";
import { EnvOverrideBanner, StorageSection } from "../components/SettingsDrawer";
import { Alert, Page, Skeleton } from "../components/ui/form-controls";
import { ThemeToggle } from "../components/ThemeToggle";
import { ModelSettings } from "../features/models/ModelSettings";

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
    <Page title="Settings">
      <section className="mb-4 max-w-[720px]">
        <h2 className="mb-2 text-base font-semibold">Appearance</h2>
        <ThemeToggle />
      </section>

      <div className="mb-8 max-w-5xl">
        <ModelSettings />
      </div>

      {loading && (
        <div className="max-w-[720px]" aria-busy="true">
          <Skeleton className="h-24" />
        </div>
      )}
      {error && (
        <Alert variant="error" className="max-w-[720px]">
          Error: {error}
        </Alert>
      )}

      {settings && (
        <div className="max-w-[720px]">
          {settings.env_overrides_active && <EnvOverrideBanner />}

          <StorageSection
            settings={settings}
            onUpdated={(next) => setSettings(next)}
          />
        </div>
      )}
    </Page>
  );
}
