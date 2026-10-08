import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { DraftWire, PlanMessageWire, SpecWire } from "../api";
import { Conversation, fixRequest } from "../Conversation";
import { DraftPanel } from "../DraftPanel";

afterEach(cleanup);

function wrap(ui: ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);
}

const spec: SpecWire = { version: 1, requirements: [], decisions: [], rules: [] } as unknown as SpecWire;

const msg = (over: Partial<PlanMessageWire>): PlanMessageWire => ({
  id: 1, at: 1_700_000_000, author: "pm", text: "Drafted.", draft_id: "draft-1", mode: "update", ...over,
});

describe("Jack's self-repair in Studio", () => {
  it("shows a change's warnings without the change prefix", () => {
    const draft: DraftWire = {
      id: "draft-1", base_version: 1, status: "open",
      changes: [{
        cid: "c1", accepted: null, by: "pm", touched: false,
        change: { op: "add_decision", ref: "new:1", title: "Storage", choice: "SQLite", reason: "short" },
        warnings: ["change 1 (add_decision): reason must be at least 20 characters and say why"],
      }],
    } as DraftWire;
    wrap(<DraftPanel draft={draft} spec={spec} onTick={() => {}} onApprove={() => {}} onDiscard={() => {}} />);
    const warnings = within(screen.getByTestId("change-c1")).getByRole("list", { name: "Warnings" });
    expect(warnings).toHaveTextContent("⚠ reason must be at least 20 characters");
    expect(warnings).not.toHaveTextContent("change 1");
  });

  it("marks a partial turn, shows its cost and asks for a repair of only what was left out", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    const problems = ["change 2 (add_item): unknown requirement R99"];
    wrap(<Conversation messages={[msg({ id: 1, author: "human", text: "plan it" }),
      msg({ id: 2, status: "partial", problems, cost_usd: 0.42 })]}
      pending={false} onSend={onSend} onStart={async () => {}} error={null} currentDraftId="draft-1" />);
    expect(screen.getByTestId("turn-status-2")).toHaveTextContent("some changes left out");
    expect(screen.getByTestId("msg-2")).toHaveTextContent("$0.42");
    fireEvent.click(screen.getByRole("button", { name: /to fix these/ }));
    expect(onSend).toHaveBeenCalledWith(fixRequest(problems), "update");
  });

  it("offers no repair once a newer message follows", () => {
    wrap(<Conversation messages={[msg({ id: 2, status: "partial", problems: ["x"] }),
      msg({ id: 3, author: "human", text: "never mind" })]}
      pending={false} onSend={vi.fn()} onStart={async () => {}} error={null} currentDraftId="draft-1" />);
    expect(screen.queryByRole("button", { name: /to fix these/ })).toBeNull();
  });
});
