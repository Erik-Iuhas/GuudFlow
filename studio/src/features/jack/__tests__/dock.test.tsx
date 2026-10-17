import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { JackDock } from "../JackDock";
import { JackProvider, useJackPlace, type JackPlace } from "../context";
import type { PlanMessageWire } from "../../plan/api";

let calls: { method: string; path: string; body: unknown }[];
let plan: { messages: PlanMessageWire[]; draft: unknown };

const reply = (actions: PlanMessageWire["actions"]): PlanMessageWire => ({
  id: 2, at: 2, author: "pm", text: "dev asks whether to run bun test plain. Yes.", draft_id: null, mode: "discuss",
  actions,
});

beforeEach(() => {
  calls = [];
  plan = { messages: [], draft: null };
  localStorage.setItem("guudflow.token", "tok");
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body });
    let json: unknown = { ok: true };
    if (path.endsWith("/plan") && method === "GET") json = plan;
    else if (path.endsWith("/plan/messages")) {
      json = { messages: [{ id: 1, at: 1, author: "human", text: body.text, draft_id: null, mode: body.mode,
        context: { ...body.context, label: "Run · run_x" } }, reply([
        { kind: "answer", label: "Answer: run it plain", run_id: "run_x", text: "Run bun test plain." },
        { kind: "stop", label: "Stop the run", run_id: "run_x" }])], draft: null };
    } else if (path === "/api/projects") json = [{ project_id: "pacrogue" }, { project_id: "recipes" }];
    else if (path.includes("/plan/preview")) json = { active: false, changes: [] };
    return { ok: true, status: 200, statusText: "ok", headers: new Headers({ "content-type": "application/json" }),
      json: async () => json } as Response;
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

function Page({ place }: { place: JackPlace | null }) {
  useJackPlace(place);
  return null;
}

function wrap(place: JackPlace | null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MemoryRouter><JackProvider>
      <Page place={place} /><JackDock />
    </JackProvider></MemoryRouter></QueryClientProvider>);
}
const writes = () => calls.filter((c) => c.method !== "GET");

describe("Jack dock", () => {
  it("sends with the page as context, and Jack's buttons call the run's routes", async () => {
    wrap({ projectId: "pacrogue", page: "run", run_id: "run_x", card: "T3" });
    fireEvent.click(screen.getByTestId("jack-launcher"));
    const dock = screen.getByTestId("jack-dock");
    expect(within(dock).getByTestId("jack-chip")).toHaveTextContent("Looking at: Run · run_x · T3");
    fireEvent.change(within(dock).getByLabelText(/Message/), { target: { value: "Why is the run paused?" } });
    fireEvent.click(within(dock).getByRole("button", { name: "Send" }));
    expect(within(dock).getByLabelText(/Message/)).toHaveValue("");                 // clears at once
    await waitFor(() => expect(writes()[0]).toMatchObject({ path: "/api/projects/pacrogue/plan/messages",
      body: { text: "Why is the run paused?", mode: "discuss", context: { page: "run", run_id: "run_x", card: "T3" } } }));
    fireEvent.click(await within(dock).findByTestId("jack-action-answer"));
    await waitFor(() => expect(writes()[1]).toMatchObject({ path: "/api/runs/run_x/respond", body: { answer: "Run bun test plain." } }));
    expect(await within(dock).findByText("Answered · the run carries on")).toBeInTheDocument();
    // Stop needs a second press.
    fireEvent.click(within(dock).getByTestId("jack-action-stop"));
    expect(writes()).toHaveLength(2);
    expect(within(dock).getByTestId("jack-action-stop")).toHaveTextContent("Stop the run? Press again");
    fireEvent.click(within(dock).getByTestId("jack-action-stop"));
    await waitFor(() => expect(writes()[2]).toMatchObject({ path: "/api/runs/run_x/stop" }));
  });

  it("the chip can be removed, and a small draft can be approved in the dock", async () => {
    plan = { messages: [], draft: { id: "d1", base_version: 1, status: "open", changes: [{ cid: "c1" }], groups: { g: {} } } };
    wrap({ projectId: "pacrogue", page: "spec", requirement: "R1" });
    fireEvent.click(screen.getByTestId("jack-launcher"));
    const dock = screen.getByTestId("jack-dock");
    fireEvent.click(within(dock).getByRole("button", { name: "Don't send where I am" }));
    expect(within(dock).queryByTestId("jack-chip")).toBeNull();
    fireEvent.click(await within(dock).findByRole("button", { name: "Approve now" }));
    await waitFor(() => expect(writes()[0]).toMatchObject({ path: "/api/projects/pacrogue/plan/draft/approve" }));
  });

  it("outside a project it asks which project, and Ctrl+J toggles it", async () => {
    wrap(null);
    fireEvent.keyDown(window, { key: "j", ctrlKey: true });
    const picker = await screen.findByTestId("jack-picker");
    fireEvent.click(await within(picker).findByRole("button", { name: "recipes" }));
    expect(await screen.findByTestId("jack-thread")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "j", ctrlKey: true });
    expect(screen.queryByTestId("jack-dock")).toBeNull();
  });
});
