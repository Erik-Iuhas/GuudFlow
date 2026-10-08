import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const initialize = vi.fn();
const renderMock = vi.fn();
vi.mock("mermaid", () => ({ default: { initialize: (...a: unknown[]) => initialize(...a), render: (...a: unknown[]) => renderMock(...a) } }));

import { MermaidDiagram } from "../MermaidDiagram";

describe("MermaidDiagram", () => {
  it("draws the diagram in strict mode and can show its source", async () => {
    renderMock.mockResolvedValueOnce({ svg: "<svg><text>run flow</text></svg>" });
    render(<MermaidDiagram source={"stateDiagram-v2\n  [*] --> picking"} label="F2 Run flow" />);
    await waitFor(() => expect(screen.getByTestId("mermaid-svg").innerHTML).toContain("run flow"));
    expect(initialize).toHaveBeenCalledWith(expect.objectContaining({ securityLevel: "strict" }));
    fireEvent.click(screen.getByText("Show source"));
    expect(screen.getByText(/\[\*\] --> picking/)).toBeTruthy();
  });

  it("shows the source and the error when the diagram does not parse", async () => {
    renderMock.mockRejectedValueOnce(new Error("Parse error on line 2"));
    render(<MermaidDiagram source="flowchart TD\n  A -->" label="bad" />);
    await waitFor(() => expect(screen.getByText(/could not be drawn: Parse error on line 2/)).toBeTruthy());
    expect(screen.getByText(/A -->/)).toBeTruthy();
  });
});
