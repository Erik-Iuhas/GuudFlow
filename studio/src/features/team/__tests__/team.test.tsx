import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ProjectConfig } from "../../../types";
import type { SpecWire } from "../../plan/api";
import { adherenceText } from "../../spec/api";
import { RulesPanel, ruleSource } from "../RulesPanel";
import { SeatsPanel } from "../SeatsPanel";

let calls: { method: string; path: string; body: unknown }[];
let rules: SpecWire["rules"];

beforeEach(() => {
  calls = [];
  rules = [
    { id: "ru1", text: "No new runtime deps", source: "D2", applies_to: ["*"], strength: "binding", status: "active" },
    { id: "ru2", text: "Write tests first", source: "human", applies_to: ["builder"], strength: "preference", status: "active" },
  ];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body });
    let json: unknown = {};
    if (path.endsWith("/spec")) json = { version: 1, requirements: [], decisions: [], rules };
    else if (path.includes("/adherence")) {
      json = { runs: [{ run_id: "r1", unmet: 3, fixed: 2 }, { run_id: "r2", unmet: [1], fixed: 0 }], unmet_merged: 1 };
    } else if (path.endsWith("/spec/edit")) json = { version: 2, applied: [], created: {} };
    else if (method === "PUT") json = body;
    return { ok: true, status: 200, statusText: "ok", headers: new Headers({ "content-type": "application/json" }), json: async () => json } as Response;
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });

const project = { project_id: "p1", goal: "g", seats: { lead: false, reviewer: "off" } } as unknown as ProjectConfig;

describe("SeatsPanel", () => {
  it("shows an empty Lead seat and saves taking it through the project doc", async () => {
    const onSaved = vi.fn();
    render(<SeatsPanel project={project} onSaved={onSaved} />);
    expect(screen.getByText("Empty: Jack decides and records why")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Take it" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.path).toBe("/api/projects/p1");
    expect((put.body as ProjectConfig).seats).toEqual({ lead: true, reviewer: "off" });
    expect(onSaved.mock.calls[0]![0].seats.lead).toBe(true);
  });

  it("saves the reviewer mode", async () => {
    const onSaved = vi.fn();
    render(<SeatsPanel project={{ ...project, seats: { lead: true, reviewer: "off" } }} onSaved={onSaved} />);
    expect(screen.getByText("You", { selector: "span" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Must requirements only" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect((calls.find((c) => c.method === "PUT")!.body as ProjectConfig).seats).toEqual({ lead: true, reviewer: "must" });
  });
});

describe("RulesPanel", () => {
  function renderRules() {
    render(<QueryClientProvider client={client()}><RulesPanel projectId="p1" /></QueryClientProvider>);
  }

  it("lists rules with their source and strength; decision-owned rules cannot be dropped here", async () => {
    renderRules();
    const owned = within(await screen.findByTestId("rule-ru1"));
    expect(owned.getByText("from D2")).toBeInTheDocument();
    expect(owned.getByText("binding")).toBeInTheDocument();
    expect(owned.getByText("change D2 in the plan")).toBeInTheDocument();
    expect(owned.queryByRole("button")).toBeNull();
    const mine = within(screen.getByTestId("rule-ru2"));
    expect(mine.getByText("you")).toBeInTheDocument();
    expect(mine.getByText("preference")).toBeInTheDocument();
    expect(ruleSource("something-else")).toBe("project default");
  });

  it("adds a rule through spec/edit", async () => {
    renderRules();
    fireEvent.click(await screen.findByRole("button", { name: "+ Rule" }));
    fireEvent.change(screen.getByLabelText("Rule text"), { target: { value: "Every endpoint has a test" } });
    fireEvent.change(screen.getByLabelText("Applies to"), { target: { value: "builder, reviewer" } });
    fireEvent.change(screen.getByLabelText("Strength"), { target: { value: "preference" } });
    fireEvent.click(screen.getByRole("button", { name: "Add rule" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/api/projects/p1/spec/edit")).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/spec/edit"))!.body).toEqual({
      change: { op: "add_rule", text: "Every endpoint has a test", applies_to: ["builder", "reviewer"], strength: "preference" },
    });
  });

  it("drops a rule through spec/edit", async () => {
    renderRules();
    fireEvent.click(await screen.findByRole("button", { name: "Drop rule: Write tests first" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/spec/edit"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/spec/edit"))!.body).toMatchObject({ change: { op: "drop_rule", id: "ru2" } });
  });

  it("summarises adherence from the last runs", async () => {
    renderRules();
    expect(await screen.findByTestId("adherence")).toHaveTextContent(
      "4 rule issues raised, 2 fixed in review. 1 merged with a broken binding rule.",
    );
    expect(adherenceText({ runs: [{ run_id: "x", unmet: 1 }], unmet_merged: 0 })).toBe(
      "1 rule issue raised, 0 fixed in review. 0 merged with a broken binding rule.",
    );
  });
});
