import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { BacklogList } from "../BacklogList";
import type { BacklogItemWire } from "../api";

const item = (over: Partial<BacklogItemWire>): BacklogItemWire => ({
  id: "B1", title: "Draw the ship", acceptance: [], kind: "code", size: "S", priority: 0,
  status: "open", notes: "", files_hint: [], source: "human", created_at: 0, updated_at: 0,
  run_id: null, pending: null, history: [], depends_on: [], ...over,
});

describe("assigning backlog items", () => {
  it("shows who an item is for, changes it, and assigns new items", () => {
    const onAssign = vi.fn();
    const onAdd = vi.fn();
    render(<BacklogList items={[item({ assignee: "dev" })]} selected={[]} onToggle={() => {}} onMove={() => {}}
      onDrop={() => {}} onReopen={() => {}} onAdd={onAdd} onStart={() => {}} roles={["dev", "tester"]} onAssign={onAssign} />);
    expect(screen.getByTestId("assignee-B1").textContent).toBe("for dev");
    fireEvent.change(screen.getByLabelText("Who does B1"), { target: { value: "owner" } });
    expect(onAssign).toHaveBeenCalledWith("B1", "owner");
    fireEvent.change(screen.getByLabelText("New item"), { target: { value: "Pick the colours" } });
    fireEvent.change(screen.getByLabelText("Who does it"), { target: { value: "tester" } });
    fireEvent.click(screen.getByRole("button", { name: "Add to backlog" }));
    expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ title: "Pick the colours", assignee: "tester" }));
  });
});
