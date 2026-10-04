import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeToggle } from "../../components/ThemeToggle";
import { THEME_KEY, getStoredTheme, getTheme, initTheme, resolveTheme, setTheme } from "../theme";

function mockSystemDark(dark: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: dark && query.includes("dark"),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.classList.remove("dark");
  delete document.documentElement.dataset.theme;
});
afterEach(() => vi.unstubAllGlobals());

describe("theme", () => {
  it("resolves system from the OS preference", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("defaults to system and follows prefers-color-scheme", () => {
    mockSystemDark(true);
    const stop = initTheme();
    expect(getTheme()).toBe("system");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    stop();
  });

  it("persists the choice in localStorage and applies the class on <html>", () => {
    mockSystemDark(false);
    setTheme("dark");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.dataset.theme).toBe("dark");
    setTheme("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("restores the stored choice on init", () => {
    mockSystemDark(false);
    window.localStorage.setItem(THEME_KEY, "dark");
    const stop = initTheme();
    expect(getTheme()).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    stop();
  });

  it("ignores garbage in storage", () => {
    window.localStorage.setItem(THEME_KEY, "purple");
    expect(getStoredTheme()).toBe("system");
  });

  it("survives storage that throws", () => {
    mockSystemDark(false);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(getStoredTheme()).toBe("system");
    expect(() => setTheme("dark")).not.toThrow();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    vi.restoreAllMocks();
  });
});

describe("ThemeToggle", () => {
  it("shows the current choice and changes it", () => {
    mockSystemDark(false);
    setTheme("light");
    render(<ThemeToggle />);
    expect(screen.getByRole("radio", { name: "Light" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    expect(screen.getByRole("radio", { name: "Dark" })).toHaveAttribute("aria-checked", "true");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
