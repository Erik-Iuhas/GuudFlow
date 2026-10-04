import { useState } from "react";
import { beforeAll, describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TeamGraph } from "../TeamGraph";
import { PlaybookPanel, describeEntry } from "../PlaybookPanel";
import { layoutTeam } from "../layout";
import type { CapabilityBlock, HarnessAgentConfig } from "../../../lib/capabilities";
import type { ModelTier } from "../../../types";
import { AGENTS, CAP_META, ENTRIES, ROLE_CAPS } from "./fixtures";

function Harness() {
  const [agents, setAgents] = useState<HarnessAgentConfig[]>(AGENTS);
  const [caps, setCaps] = useState(ROLE_CAPS);
  return (
    <TeamGraph
      projectId="p"
      goal="g"
      agents={agents}
      roleCaps={caps}
      capMeta={CAP_META}
      onCapabilityChange={(a, next: CapabilityBlock) =>
        setCaps((c) => ({ ...c, [a.role]: { ...c[a.role], capabilities: next } }))
      }
      onTierChange={(role, tier: ModelTier) =>
        setAgents((l) => l.map((a) => (a.role === role ? { ...a, model_tier: tier } : a)))
      }
    />
  );
}

beforeAll(() => {
  // jsdom has no SVG layout; React Flow measures edge labels with getBBox.
  (SVGElement.prototype as unknown as { getBBox: () => DOMRect }).getBBox = () =>
    ({ x: 0, y: 0, width: 80, height: 14 }) as DOMRect;
});

const costText = () => screen.getByTestId("cost-strip").textContent ?? "";

describe("layoutTeam", () => {
  it("builds columns by depth, dependency edges and labelled review edges", () => {
    const { nodes, edges } = layoutTeam(AGENTS);
    expect(nodes.map((n) => n.position.x)).toEqual([0, 310, 620]);
    expect(edges.map((e) => e.id)).toEqual([
      "dep:planner->builder", "dep:builder->reviewer", "review:reviewer->builder",
    ]);
    expect(edges[2].label).toBe("review, max 3 rounds");
  });
});

describe("TeamGraph", () => {
  it("renders role nodes with type, tier and capability summary", () => {
    render(<Harness />);
    for (const role of ["planner", "builder", "reviewer"]) {
      expect(screen.getByTestId(`team-node-${role}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId("team-node-planner")).toHaveTextContent("tier: heavy");
    expect(screen.getByTestId("team-node-builder")).toHaveTextContent("shell: project");
  });

  it("renders the review edge label", () => {
    render(<Harness />);
    expect(screen.getByText("review, max 3 rounds")).toBeInTheDocument();
  });

  it("opens a side sheet with switches and updates the cost estimate on change", () => {
    render(<Harness />);
    const before = costText();
    fireEvent.click(screen.getByTestId("team-node-builder"));
    const web = screen.getByLabelText("builder web");
    fireEvent.change(web, { target: { value: "search" } });
    expect(web).toHaveValue("search");
    expect(costText()).not.toBe(before);
    expect(screen.getByTestId("team-node-builder")).toHaveTextContent("web: search");
  });

  it("explains each toggle in plain language on focus", async () => {
    render(<Harness />);
    fireEvent.click(screen.getByTestId("team-node-builder"));
    fireEvent.focus(screen.getByTestId("toggle-label-builder-web"));
    expect((await screen.findAllByText(/Lets this role look things up on the web/)).length).toBeGreaterThan(0);
  });

  it("updates the cost estimate when the model tier changes", () => {
    render(<Harness />);
    const before = costText();
    fireEvent.click(screen.getByTestId("team-node-builder"));
    fireEvent.change(screen.getByLabelText("builder model tier"), { target: { value: "heavy" } });
    expect(costText()).not.toBe(before);
    expect(screen.getByTestId("team-node-builder")).toHaveTextContent("tier: heavy");
  });
});

describe("PlaybookPanel", () => {
  it("renders diff-style entries with confidence and evidence counts", () => {
    render(<PlaybookPanel entries={ENTRIES} />);
    const rows = screen.getAllByTestId("playbook-entry");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("+ brief addendum for builder: Read research/notes.md first");
    expect(rows[0]).toHaveTextContent("confidence 80%");
    expect(rows[0]).toHaveTextContent("2 runs");
    expect(rows[1]).toHaveTextContent("~ review rounds builder→reviewer: 3");
    expect(rows[1]).toHaveTextContent("1 run");
    expect(screen.getByText(/Nothing is applied until you approve/)).toBeInTheDocument();
  });

  it("describes pitfalls and shows an empty state", () => {
    expect(describeEntry({ ...ENTRIES[0], section: "known_pitfalls" }).marker).toBe("!");
    render(<PlaybookPanel entries={[]} />);
    expect(screen.getByText("No playbook entries were used.")).toBeInTheDocument();
  });
});
