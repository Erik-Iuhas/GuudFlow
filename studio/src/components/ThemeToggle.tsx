import { THEME_PREFERENCES, useTheme, type ThemePreference } from "../lib/theme";
import { cn } from "../lib/utils";

const LABELS: Record<ThemePreference, string> = {
  light: "Light",
  dark: "Dark",
  system: "System",
};

/** Light / dark / system selector, persisted by `lib/theme`. */
export function ThemeToggle() {
  const [theme, setTheme] = useTheme();
  return (
    <div
      role="radiogroup"
      aria-label="Theme"
      className="inline-flex gap-1 rounded-md bg-muted p-1"
      data-testid="theme-toggle"
    >
      {THEME_PREFERENCES.map((pref) => {
        const active = theme === pref;
        return (
          <button
            key={pref}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => setTheme(pref)}
            className={cn(
              "rounded-sm border border-transparent px-3 py-1 text-sm font-medium transition-colors hover:border-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active
                ? "bg-background text-foreground shadow-sm hover:bg-background"
                : "bg-transparent text-muted-foreground hover:bg-accent hover:text-accent-foreground",
            )}
          >
            {LABELS[pref]}
          </button>
        );
      })}
    </div>
  );
}
