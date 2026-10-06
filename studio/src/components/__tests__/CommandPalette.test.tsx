import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { CommandPalette, filterItems, lastRuns, PALETTE_ROUTES } from "../CommandPalette";

vi.mock("../../api", () => ({
  runsApi: {
    list: vi.fn().mockResolvedValue([
      { run_id: "run-old", project_id: "p", status: "complete", created_at: "2026-01-01T00:00:00" },
      { run_id: "run-new", project_id: "p", status: "running", created_at: "2026-02-01T00:00:00", goal: "ship it" },
    ]),
  },
}));

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function setup(onOpenChange = vi.fn()) {
  render(
    <MemoryRouter>
      <CommandPalette open onOpenChange={onOpenChange} />
      <Where />
    </MemoryRouter>,
  );
  return onOpenChange;
}

describe("CommandPalette", () => {
  it("lists every route and the latest runs first", async () => {
    setup();
    for (const r of PALETTE_ROUTES) expect(screen.getByRole("option", { name: new RegExp(`^${r.label}`) })).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("option", { name: /run-new/ })).toBeTruthy());
    const ids = screen.getAllByRole("option").map((o) => o.textContent ?? "");
    expect(ids.findIndex((t) => t.includes("run-new"))).toBeLessThan(ids.findIndex((t) => t.includes("run-old")));
  });

  it("filters and opens the highlighted item with Enter", () => {
    const onOpenChange = setup();
    const input = screen.getByRole("combobox", { name: "Search routes and runs" });
    fireEvent.change(input, { target: { value: "roster" } });
    expect(screen.getAllByRole("option")).toHaveLength(1);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("where").textContent).toBe("/roster");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("clicking a run opens its cockpit", async () => {
    setup();
    await waitFor(() => screen.getByRole("option", { name: /run-new/ }));
    fireEvent.click(screen.getByRole("option", { name: /run-new/ }));
    expect(screen.getByTestId("where").textContent).toBe("/runs/run-new");
  });

  it("shows an empty state when nothing matches", () => {
    setup();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "zzzz" } });
    expect(screen.getByText(/Nothing matches/)).toBeTruthy();
  });

  it("helpers sort and filter", () => {
    expect(lastRuns([{ run_id: "a", project_id: "p", status: "x" }, { run_id: "b", project_id: "p", status: "x", created_at: "2" }]).map((r) => r.run_id)).toEqual(["b", "a"]);
    expect(filterItems(PALETTE_ROUTES, "SET").map((i) => i.label)).toEqual(["Settings"]);
  });
});
