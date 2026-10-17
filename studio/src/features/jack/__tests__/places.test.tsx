import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { JackDock } from "../JackDock";
import { JackProvider } from "../context";
import { SpecPageView } from "../../plan/SpecPage";
import type { SpecWire } from "../../plan/api";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, statusText: "ok",
    headers: new Headers({ "content-type": "application/json" }), json: async () => ({ messages: [], draft: null }) }) as unknown as Response));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const spec = {
  version: 2, decisions: [], rules: [],
  requirements: [
    { id: "R1", text: "The board shows", title: "Board", kind: "feature", priority: "must", status: "active", checks: [], params: [] },
    { id: "R2", text: "Scores add up", title: "Score", kind: "feature", priority: "must", status: "active", checks: [], params: [] },
  ],
} as unknown as SpecWire;

describe("pages tell the dock what is on screen", () => {
  it("the Spec page's selected requirement becomes the chip", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><MemoryRouter><JackProvider>
      <SpecPageView spec={spec} projectId="pacrogue" /><JackDock />
    </JackProvider></MemoryRouter></QueryClientProvider>);
    fireEvent.click(screen.getByTestId("req-R2"));
    fireEvent.click(screen.getByTestId("jack-launcher"));
    expect(screen.getByTestId("jack-chip")).toHaveTextContent("Looking at: Spec · R2");
  });
});
