import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { TraceWire } from "../api";
import { exportUrl } from "../api";
import { linkify, versionCount } from "../model";
import { RecordTab } from "../RecordTab";

const traces: Record<string, TraceWire> = {
  R1: {
    ref: "R1",
    events: [
      { at: 1_700_000_000, kind: "spec", ref: "R1", text: "R1 v1 added (must feature): Users can sign in", by: "erik", links: ["draft-1"] },
      { at: 1_700_000_100, kind: "decision", ref: "R1", text: "D1 serves R1: Storage (accepted)", by: "pm", links: ["D1"] },
      { at: 1_700_000_200, kind: "session", ref: "R1", text: "Session run_a accepted: looks good", by: "human", links: ["run_a"] },
      { at: 1_700_000_300, kind: "status", ref: "R1", text: "R1 is active, v1; 1/1 checks met; items: B1 (done)", by: "", links: ["B1"] },
    ],
  },
  D1: { ref: "D1", events: [{ at: 1_700_000_050, kind: "decision", ref: "D1", text: "D1 accepted", by: "erik", links: [] }] },
};

interface Server {
  calls: { method: string; path: string; body: unknown; auth?: string }[];
  project: Record<string, unknown>;
}
let server: Server;
let clicked: { href: string; download: string }[];

beforeEach(() => {
  server = { calls: [], project: { project_id: "p1", goal: "x" } };
  clicked = [];
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined }));
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    clicked.push({ href: this.href, download: this.download });
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
      server.calls.push({ method, path, body, auth });
      let status = 200;
      let json: unknown = {};
      let text = "";
      const headers = new Headers({ "content-type": "application/json" });
      if (path.startsWith("/api/projects/p1/record?ref=")) {
        const ref = decodeURIComponent(path.split("ref=")[1]);
        if (traces[ref]) json = traces[ref];
        else [status, json] = [404, { detail: `unknown requirement '${ref}'` }];
      } else if (path.endsWith("/record/spec-files")) {
        json = { files: { "SPEC.md": "# Spec\n\n- [x] R1.1 <b>login</b>\n", "docs/decisions/0001-storage.md": "# D1" } };
      } else if (path.includes("/record/export")) {
        text = "exported";
        headers.set("Content-Disposition", 'attachment; filename="p1-record.md"');
      } else if (path.endsWith("/spec")) {
        json = { version: 1, requirements: [{ id: "R1", text: "Users can sign in" }], decisions: [{ id: "D1", title: "Storage" }], rules: [] };
      } else if (method === "GET" && path === "/api/projects/p1") json = server.project;
      else if (method === "PUT" && path === "/api/projects/p1") {
        server.project = body as Record<string, unknown>;
        json = server.project;
      } else status = 404;
      return {
        ok: status < 400,
        status,
        statusText: "x",
        headers,
        json: async () => json,
        blob: async () => new Blob([text]),
      } as unknown as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <RecordTab projectId="p1" />
    </QueryClientProvider>,
  );
}

describe("record model", () => {
  it("linkifies ids and extra links", () => {
    const parts = linkify("D1 serves R1.2 in run_a and B7.", ["run_a"]);
    expect(parts.filter((p) => p.ref).map((p) => p.ref)).toEqual(["D1", "R1.2", "run_a", "B7"]);
    expect(parts.map((p) => p.text).join("")).toBe("D1 serves R1.2 in run_a and B7.");
    expect(linkify("nothing here")).toEqual([{ text: "nothing here" }]);
  });

  it("counts versions and builds the export url in a stable order", () => {
    expect(versionCount(traces.R1.events)).toBe("2 versions");
    expect(versionCount([{ at: 1, kind: "work", ref: "B1", text: "x", by: "", links: [] }])).toBe("1 event");
    expect(exportUrl("p1", ["costs", "spec"], "json")).toBe("/api/projects/p1/record/export?format=json&include=spec,costs");
  });
});

describe("RecordTab trace", () => {
  it("renders the timeline and re-traces on an id click", async () => {
    renderTab();
    const input = screen.getByPlaceholderText("Trace R3, D4, B7, a session or a file");
    fireEvent.change(input, { target: { value: "R1" } });
    fireEvent.click(screen.getByRole("button", { name: "Trace" }));

    const trace = await screen.findByTestId("trace");
    expect(await within(trace).findByRole("heading", { name: "R1 Users can sign in" })).toBeInTheDocument();
    expect(within(trace).getByText(/2 versions · how it changed, who changed it, what it touched/)).toBeInTheDocument();
    const events = within(trace).getAllByTestId("event");
    expect(events).toHaveLength(4);
    expect(within(events[0]).getByText("2023-11-14 22:13")).toBeInTheDocument();
    expect(within(events[1]).getByText("decision")).toBeInTheDocument();
    expect(within(events[3]).getByText("now")).toBeInTheDocument();

    fireEvent.click(within(events[1]).getByRole("button", { name: "D1" }));
    await waitFor(() => expect(screen.getByRole<HTMLInputElement>("textbox", { name: "Trace" }).value).toBe("D1"));
    expect(await screen.findByRole("heading", { name: "D1 Storage" })).toBeInTheDocument();
    expect(server.calls.some((c) => c.path === "/api/projects/p1/record?ref=D1")).toBe(true);
  });

  it("shows the server's error for an unknown ref", async () => {
    renderTab();
    fireEvent.change(screen.getByRole("textbox", { name: "Trace" }), { target: { value: "R9" } });
    fireEvent.click(screen.getByRole("button", { name: "Trace" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("unknown requirement 'R9'");
  });
});

describe("RecordTab export", () => {
  it("downloads with the ticked sections and the format", async () => {
    renderTab();
    expect(screen.getByLabelText("Spec with history")).toBeChecked();
    expect(screen.getByLabelText("Plan conversations")).not.toBeChecked();
    fireEvent.click(screen.getByLabelText("Costs"));
    fireEvent.click(screen.getByLabelText("Decisions"));
    fireEvent.click(screen.getByRole("button", { name: "JSON" }));
    fireEvent.click(screen.getByRole("button", { name: "Download" }));
    await waitFor(() => expect(clicked).toHaveLength(1));
    const call = server.calls.find((c) => c.path.includes("/record/export"));
    expect(call?.path).toBe("/api/projects/p1/record/export?format=json&include=spec,sessions,costs");
    expect(clicked[0].download).toBe("p1-record.md");
  });

  it("previews SPEC.md as plain text", async () => {
    renderTab();
    const pre = await screen.findByLabelText("SPEC.md preview");
    expect(pre).toHaveTextContent("- [x] R1.1 <b>login</b>");
    expect(pre.querySelector("b")).toBeNull();
    expect(screen.getByText(/\+ 1 decision file/)).toBeInTheDocument();
  });

  it("saves keep-in-repo into the project doc", async () => {
    server.project = { project_id: "p1", goal: "x", seats: { lead: true } };
    renderTab();
    const box = await screen.findByLabelText(/Keep SPEC.md and docs\/decisions\/ in the project repo/);
    await waitFor(() => expect(box).not.toBeDisabled());
    expect(box).not.toBeChecked();
    fireEvent.click(box);
    await waitFor(() => expect(server.calls.some((c) => c.method === "PUT")).toBe(true));
    const put = server.calls.find((c) => c.method === "PUT");
    expect(put?.body).toEqual({ project_id: "p1", goal: "x", seats: { lead: true }, record: { keep_in_repo: true } });
    await waitFor(() => expect(box).toBeChecked());
  });

  it("shows the saved keep-in-repo setting", async () => {
    server.project = { project_id: "p1", goal: "x", record: { keep_in_repo: true } };
    renderTab();
    await waitFor(() => expect(screen.getByLabelText(/Keep SPEC.md/)).toBeChecked());
  });
});
