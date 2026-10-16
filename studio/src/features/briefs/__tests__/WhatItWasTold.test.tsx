import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { WhatItWasTold } from "../WhatItWasTold";

afterEach(() => vi.unstubAllGlobals());

describe("WhatItWasTold", () => {
  it("shows the newest turn of the card with its rules and brief", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(
      url.endsWith("/briefs")
        ? { roles: { dev: [{ n: 1, card: "T1", at: 1, size: 9 }, { n: 2, card: "T2", at: 2, size: 9 }] } }
        : { text: "## Rules file: guudflow.md\n\nUse Python.\n\n## Brief\n\nDo T2." },
    ))));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><WhatItWasTold runId="r1" role="dev" card="T2" /></QueryClientProvider>);
    expect(await screen.findByText("Use Python.")).toBeTruthy();
    expect(screen.getAllByRole("option")).toHaveLength(1);
  });
});
