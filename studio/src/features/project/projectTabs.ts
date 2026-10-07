/** Project page tabs (project flow plan §7) and the mapping of older `?tab=` values. */
export const PROJECT_TABS = ["work", "plan", "spec", "sessions", "team", "record", "settings"] as const;
export type ProjectTab = (typeof PROJECT_TABS)[number];

export const TAB_LABEL: Record<ProjectTab, string> = {
  work: "Work",
  plan: "Plan",
  spec: "Spec",
  sessions: "Sessions",
  team: "Team",
  record: "Record",
  settings: "Settings",
};

/** Old tab names that now live inside another tab. */
const LEGACY_TABS: Record<string, ProjectTab> = {
  inputs: "plan",
  roadmap: "plan",
  insights: "record",
};

/** A `?tab=` value as a tab: current names pass through, old ones map, anything else is Work. */
export function normalizeTab(value: string | null | undefined): ProjectTab {
  if (!value) return "work";
  if ((PROJECT_TABS as readonly string[]).includes(value)) return value as ProjectTab;
  return LEGACY_TABS[value] ?? "work";
}
