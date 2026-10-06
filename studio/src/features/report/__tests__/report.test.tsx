import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { createRunStore } from "../../../stores/runEvents";
import { RunStoreProvider } from "../../../stores/runStoreContext";
import { RunReportView } from "../RunReport";
import { DiffText } from "../DiffViewer";
import { costBars, formatSpan } from "../model";
import type { RunReportData } from "../api";
import { REPORT_FIXTURE, SAMPLE_DIFF } from "../__fixtures__/report";

function wrap(report: RunReportData) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <RunStoreProvider store={createRunStore(report.run_id)}>
          <RunReportView report={report} />
        </RunStoreProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe("RunReportView", () => {
  it("renders every section from the payload", () => {
    wrap(REPORT_FIXTURE);
    const summary = screen.getByTestId("section-summary");
    expect(summary).toHaveTextContent("complete");
    expect(summary).toHaveTextContent("12m 03s");
    expect(summary).toHaveTextContent("2 / 3");
    expect(summary).toHaveTextContent("3.5 min");

    const rows = screen.getAllByTestId("cost-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("dev");
    expect(rows[0]).toHaveTextContent("12");
    expect(rows[0]).toHaveTextContent("120k");
    expect(rows[0]).toHaveTextContent("$1.23");
    expect(screen.getByTestId("cost-table")).toHaveTextContent("$1.38");

    expect(screen.getByTestId("branch")).toHaveTextContent("agent/dev");
    expect(screen.getByText("feat: add forecast endpoint")).toBeInTheDocument();

    expect(screen.getByTestId("web-table")).toHaveTextContent("open-meteo hourly format");
    expect(screen.getByTestId("web-table")).toHaveTextContent("refused: private address");

    const caps = screen.getByTestId("capability-table");
    expect(caps).toHaveTextContent("web: search");
    expect(caps).toHaveTextContent("shell: off");

    const retro = screen.getByTestId("section-retrospective");
    expect(retro).toHaveTextContent("lint errors in api.py");
    expect(retro).toHaveTextContent("dev->reviewer: 2");
    const ivs = within(retro).getAllByTestId("report-intervention");
    expect(ivs[0]).toHaveTextContent("dev kept guessing the response shape");
    expect(ivs[0]).toHaveTextContent("worked");
    expect(ivs[1]).toHaveTextContent("did not work");
    expect(ivs[1]).toHaveTextContent("needs human");

    expect(screen.getByTestId("carry-forward")).toHaveTextContent("Still open: T3 Docs");
  });

  it("degrades while the run has no retrospective or data", () => {
    wrap({
      ...REPORT_FIXTURE, retrospective: null, carry_forward: null, web: [], capabilities: [], branches: [],
      interventions: [], costs: { rows: [], total: { ...REPORT_FIXTURE.costs.total, cost_usd: 0 } },
      first_signal_at: null, last_signal_at: null,
    });
    expect(screen.getByTestId("section-summary")).toHaveTextContent("not available yet");
    expect(screen.getByText(/No model calls were recorded/)).toBeInTheDocument();
    expect(screen.getByText(/No retrospective yet/)).toBeInTheDocument();
    expect(screen.getByText(/did not intervene/)).toBeInTheDocument();
    expect(screen.getByText(/No carry-forward/)).toBeInTheDocument();
    expect(screen.getByText(/No agent used the web/)).toBeInTheDocument();
  });

  it("loads a commit diff on demand and renders it", async () => {
    const fetchMock = vi.fn(async (_url: string) => new Response(JSON.stringify({ sha: "a".repeat(40), diff: SAMPLE_DIFF }), {
      status: 200, headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(REPORT_FIXTURE);
    fireEvent.click(screen.getByRole("button", { name: /Show diff/ }));
    await waitFor(() => expect(screen.getByTestId("diff-viewer")).toBeInTheDocument());
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(`/api/runs/run_rep/git/diff?ref=${"a".repeat(40)}`);
    expect(screen.getByTestId("diff-viewer")).toHaveTextContent("print('extra')");
    vi.unstubAllGlobals();
  });
});

describe("DiffText", () => {
  it("falls back to plain text for something that is not a diff", () => {
    render(<DiffText text="(could not render diff: boom)" />);
    expect(screen.queryByTestId("diff-viewer")).toBeNull();
    expect(screen.getByText(/could not render diff/)).toBeInTheDocument();
  });
});

describe("model", () => {
  it("scales bars to the biggest spender by tokens", () => {
    const bars = costBars(REPORT_FIXTURE.costs.rows);
    expect(bars[0]?.pct).toBe(100);
    expect(bars[1]?.pct).toBe(15);
  });

  it("formats spans", () => {
    expect(formatSpan("2026-03-01T10:00:00Z", "2026-03-01T11:05:00Z")).toBe("1h 05m");
    expect(formatSpan(null, "2026-03-01T11:05:00Z")).toBeNull();
    expect(formatSpan("2026-03-01T11:05:00Z", "2026-03-01T10:00:00Z")).toBeNull();
  });
});
