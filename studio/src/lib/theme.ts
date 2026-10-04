import { useSyncExternalStore } from "react";

/** User choice. "system" follows `prefers-color-scheme`. */
export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_KEY = "guudflow.theme";
export const THEME_PREFERENCES: readonly ThemePreference[] = ["light", "dark", "system"];

function isPreference(v: unknown): v is ThemePreference {
  return v === "light" || v === "dark" || v === "system";
}

/** Read the stored preference. Storage can throw (private mode, blocked), so never trust it. */
export function getStoredTheme(): ThemePreference {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    return isPreference(v) ? v : "system";
  } catch {
    return "system";
  }
}

function storeTheme(pref: ThemePreference): void {
  try {
    window.localStorage.setItem(THEME_KEY, pref);
  } catch {
    // Persistence is a convenience; the theme still applies for this session.
  }
}

function systemPrefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/** Turn a preference into the concrete theme to render. */
export function resolveTheme(pref: ThemePreference, prefersDark: boolean = systemPrefersDark()): ResolvedTheme {
  if (pref === "system") return prefersDark ? "dark" : "light";
  return pref;
}

let current: ThemePreference = "system";
const listeners = new Set<() => void>();

function applyToDocument(pref: ThemePreference): void {
  const resolved = resolveTheme(pref);
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.dataset.theme = resolved;
}

function notify(): void {
  for (const l of listeners) l();
}

/** Set, apply and persist the theme preference. */
export function setTheme(pref: ThemePreference): void {
  current = pref;
  storeTheme(pref);
  applyToDocument(pref);
  notify();
}

/** Current preference (in-memory; seeded by `initTheme`). */
export function getTheme(): ThemePreference {
  return current;
}

/**
 * Apply the stored preference and follow OS changes while the preference is
 * "system". Call once at startup. Returns a cleanup function.
 */
export function initTheme(): () => void {
  current = getStoredTheme();
  applyToDocument(current);
  notify();
  if (typeof window.matchMedia !== "function") return () => {};
  const mql = window.matchMedia("(prefers-color-scheme: dark)");
  const onChange = () => {
    if (current === "system") applyToDocument(current);
  };
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/** React binding: `[preference, setTheme]`. */
export function useTheme(): [ThemePreference, (pref: ThemePreference) => void] {
  const pref = useSyncExternalStore(subscribe, getTheme, getTheme);
  return [pref, setTheme];
}
