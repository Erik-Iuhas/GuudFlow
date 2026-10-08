import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BacklogList } from "../BacklogList";
import type { BacklogItemWire } from "../api";

const item = (over: Partial<BacklogItemWire>): BacklogItemWire => ({
  id: "B1", title: "Rename config keys", acceptance: [], kind: "code", size: "S", priority: 0,
  status: "open", notes: "", files_hint: [], source: "human", created_at: 0, updated_at: 0,
  run_id: null, pending: null, history: [], depends_on: [], ...over,
});

describe("item effort", () => {
  it("shows the effort and lets a person change it", () => {
    const onEffort = vi.fn();
    render(<BacklogList items={[item({ effort: "low" })]} selected={[]} onToggle={() => {}} onMove={() => {}}
      onDrop={() => {}} onReopen={() => {}} onEffort={onEffort} onAdd={() => {}} onStart={() => {}} />);
    expect(screen.getByTestId("effort-B1").textContent).toBe("low effort");
    fireEvent.change(screen.getByLabelText("Effort for B1"), { target: { value: "high" } });
    expect(onEffort).toHaveBeenCalledWith("B1", "high");
  });
});
