import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Propose } from "../Propose";

const levels = {
  sandbox: ["strict", "standard", "host_dev"],
  web: ["off", "search", "search+fetch"],
  shell: ["readonly", "project", "ask"],
  packages: ["off", "via_it_agent"],
  subagents: ["off", "explore_only", "full"],
  browser: ["off", "screenshot"],
  vision: ["off", "on"],
};
const builder = {
  sandbox: "standard", web: "off", shell: "project", packages: "via_it_agent",
  subagents: "explore_only", browser: "off", vision: "off",
};

const mocks = vi.hoisted(() => ({
  propose: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  capabilities: vi.fn(),
  resolve: vi.fn(),
  playbook: vi.fn(),
}));

vi.mock("../../api", () => {
  class ApiError extends Error {
    constructor(public status: number, public detail: string) {
      super(`${status}: ${detail}`);
    }
  }
  return {
    ApiError,
    metaApi: { propose: mocks.propose },
    projectsApi: { create: mocks.create, update: mocks.update, playbook: mocks.playbook },
    harnessApi: { capabilities: mocks.capabilities, resolve: mocks.resolve },
  };
});
vi.mock("../../components/WorkerCard", () => ({
  WorkerCard: ({ agent }: { agent: { role: string } }) => <div>card {agent.role}</div>,
}));
vi.mock("../../components/CostStrip", () => ({ CostStrip: () => <div /> }));

const PROPOSAL = {
  rationale: "because",
  agents: [{ role: "dev", model_tier: "solid" }],
  rbac: {},
};

async function proposeTeam() {
  render(
    <MemoryRouter>
      <Propose />
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByPlaceholderText(/Build an auth flow/), {
    target: { value: "build it" },
  });
  fireEvent.click(screen.getByText("Propose team"));
  await screen.findByText("Proposed team");
}

describe("Propose capability switches", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.propose.mockResolvedValue(PROPOSAL);
    mocks.create.mockResolvedValue({});
    mocks.capabilities.mockResolvedValue({
      levels,
      role_types: ["builder"],
      defaults: { builder },
      host_dev_warning: "warn",
      production: false,
    });
    mocks.resolve.mockImplementation(async (req: { capabilities?: Record<string, string> }) => ({
      role_type: "builder",
      capabilities: { ...builder, ...(req.capabilities ?? {}) },
      vision_available: true,
      errors: [],
      warnings: [],
    }));
  });

  it("saves harness opencode explicitly (O15)", async () => {
    await proposeTeam();
    expect(screen.queryByLabelText("Agent harness")).toBeNull();
    fireEvent.click(screen.getByText("Approve & create"));
    await waitFor(() => expect(mocks.create).toHaveBeenCalled());
    expect(mocks.create.mock.calls[0][0].harness).toBe("opencode");
  });

  it("shows switches for opencode and persists role_type + capabilities", async () => {
    await proposeTeam();
    // Graph view is the default: capabilities live in the node's side sheet.
    await waitFor(() => expect(mocks.resolve).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId("team-node-dev"));
    const web = await screen.findByLabelText("dev web");
    fireEvent.change(web, { target: { value: "search" } });
    await waitFor(() =>
      expect((screen.getByLabelText("dev web") as HTMLSelectElement).value).toBe("search"),
    );
    await waitFor(() => expect(mocks.resolve).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByText("Approve & create"));
    await waitFor(() => expect(mocks.create).toHaveBeenCalled());
    const saved = mocks.create.mock.calls[0][0];
    expect(saved.harness).toBe("opencode");
    expect(saved.agents[0].role_type).toBe("builder");
    expect(saved.agents[0].capabilities).toEqual({ ...builder, web: "search" });
  });

  it("keeps the list view behind the List toggle", async () => {
    await proposeTeam();
    expect(screen.getByTestId("team-graph")).toBeTruthy();
    expect(screen.queryByText("card dev")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "List" }));
    expect(screen.getByText("card dev")).toBeTruthy();
    expect(screen.queryByTestId("team-graph")).toBeNull();
  });

  it("shows playbook entries that shaped a continuation proposal", async () => {
    mocks.propose.mockResolvedValue({ ...PROPOSAL, playbook_entries_used: ["e1"] });
    mocks.playbook.mockResolvedValue({
      entries: [
        { id: "e1", section: "brief_addenda", key: "dev", value: "Read the notes", confidence: 0.8,
          state: "active", evidence_runs: ["r1"] },
        { id: "e2", section: "known_pitfalls", key: "dev", value: "unused", confidence: 0.5,
          state: "active", evidence_runs: ["r1"] },
      ],
      metrics: [],
    });
    render(
      <MemoryRouter initialEntries={["/propose?project_id=p1"]}>
        <Propose />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByPlaceholderText(/Build an auth flow|Add a refresh-token|e\.g\./), {
      target: { value: "build it" },
    });
    fireEvent.click(screen.getByText("Propose team"));
    const rows = await screen.findAllByTestId("playbook-entry");
    expect(rows).toHaveLength(1);
    expect(rows[0].textContent).toContain("brief addendum for dev: Read the notes");
    expect(mocks.playbook).toHaveBeenCalledWith("p1");
  });

  it("shows the server's validation error under the switches", async () => {
    const { ApiError } = await import("../../api");
    await proposeTeam();
    fireEvent.click(screen.getByRole("button", { name: "List" }));
    const sandbox = await screen.findByLabelText("dev sandbox");
    mocks.resolve.mockRejectedValueOnce(new ApiError(422, "bad capability"));
    fireEvent.change(sandbox, { target: { value: "host_dev" } });
    expect(await screen.findByText("bad capability")).toBeTruthy();
  });
});
