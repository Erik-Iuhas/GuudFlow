import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { PreflightWire } from "../api";
import { PreflightPanel } from "../PreflightPanel";
import { PrepareSession } from "../PrepareSession";
import { PlanCheckpoint } from "../../cockpit/PlanCheckpoint";

const PLAN: PreflightWire = {
  id: "P1", status: "draft", items: ["B1"], goal: "", planner: "tech_lead",
  plan: { tasks: [{ id: "T1", title: "Draw the board", owner_role: "dev", acceptance: ["the board shows"], size: "S", kind: "code" }] },
  assumptions: [
    { id: "A1", text: "How big is the window?", decided_by: "owner", choice: "800x600", answer: "" },
    { id: "A2", text: "Use pygame for drawing", decided_by: "tech_lead", choice: "pygame", answer: "" },
  ],
  messages: [{ author: "tech_lead", text: "Here is the plan.", at: 1 }],
  spend_usd: 0.12, problems: [], stale: [], open_questions: ["A1"],
};

let calls: { method: string; path: string; body: unknown }[];
let current: PreflightWire | null;

beforeEach(() => {
  calls = [];
  current = PLAN;
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body });
    if (path.endsWith("/assumptions/A1")) current = { ...PLAN, assumptions: [{ ...PLAN.assumptions[0], answer: "1024x768" }, PLAN.assumptions[1]], open_questions: [] };
    if (path.endsWith("/approve")) current = { ...(current as PreflightWire), status: "approved" };
    if (path.endsWith("/prepare")) current = PLAN;
    return { ok: true, status: 200, statusText: "ok", headers: new Headers(), json: async () => ({ plan: current }) } as Response;
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}
const writes = () => calls.filter((c) => c.method !== "GET");

describe("PreflightPanel", () => {
  it("blocks approval until the owner's question is answered, then approves", async () => {
    wrap(<PreflightPanel projectId="p1" />);
    const approve = await screen.findByRole("button", { name: "Approve the plan" });
    expect(approve).toBeDisabled();
    expect(screen.getByText("Waiting on your answer: A1")).toBeInTheDocument();
    const row = screen.getByTestId("assumption-A1");
    fireEvent.change(within(row).getByLabelText("Answer A1"), { target: { value: "1024x768" } });
    fireEvent.click(within(row).getByRole("button", { name: "Answer" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve the plan" })).not.toBeDisabled());
    expect(writes()[0]).toMatchObject({ path: "/api/projects/p1/preflight/assumptions/A1", body: { answer: "1024x768" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve the plan" }));
    expect(await screen.findByText(/Approved: start it from the Work tab/)).toBeInTheDocument();
  });

  it("edits cards and saves the whole list", async () => {
    wrap(<PreflightPanel projectId="p1" />);
    fireEvent.change(await screen.findByLabelText("Title of T1"), { target: { value: "Draw the 8x8 board" } });
    fireEvent.click(screen.getByRole("button", { name: "Save cards" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: "PUT", path: "/api/projects/p1/preflight/cards" });
    expect((writes()[0].body as { tasks: { title: string }[] }).tasks[0].title).toBe("Draw the 8x8 board");
  });
});

describe("PrepareSession", () => {
  it("prepares the chosen items, then starts the approved plan", async () => {
    current = null;
    const onStart = vi.fn();
    wrap(<PrepareSession projectId="p1" chosen={["B1"]} onStart={onStart} />);
    fireEvent.click(await screen.findByRole("button", { name: "Prepare with the tech lead" }));
    await waitFor(() => expect(writes()[0]).toMatchObject({ path: "/api/projects/p1/preflight/prepare", body: { items: ["B1"] } }));
    expect(await screen.findByText(/P1 is a draft with 1 question/)).toBeInTheDocument();
  });

  it("offers Start for an approved plan", async () => {
    current = { ...PLAN, status: "approved", open_questions: [] };
    const onStart = vi.fn();
    wrap(<PrepareSession projectId="p1" chosen={["B1"]} onStart={onStart} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start the prepared session" }));
    expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ id: "P1" }));
  });
});

describe("PlanCheckpoint", () => {
  it("approves and sends back with a note", async () => {
    const onEdit = vi.fn();
    wrap(<PlanCheckpoint runId="r1" role="planner" cards={["T1", "T2"]} onEdit={onEdit} />);
    expect(screen.getByText(/planner planned 2 card/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Edit the cards" }));
    expect(onEdit).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Approve the plan" }));
    await waitFor(() => expect(writes()[0]).toMatchObject({ path: "/api/runs/r1/plan/approve" }));
    fireEvent.change(screen.getByLabelText("What to change"), { target: { value: "one module" } });
    fireEvent.click(screen.getByRole("button", { name: "Send back" }));
    await waitFor(() => expect(writes()[1]).toMatchObject({ path: "/api/runs/r1/plan/send-back", body: { note: "one module" } }));
  });
});
