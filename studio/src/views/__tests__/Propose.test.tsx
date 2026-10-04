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
    projectsApi: { create: mocks.create, update: mocks.update },
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

  it("hides switches and saves no harness by default (legacy)", async () => {
    await proposeTeam();
    expect(screen.queryByLabelText("dev shell")).toBeNull();
    fireEvent.click(screen.getByText("Approve & create"));
    await waitFor(() => expect(mocks.create).toHaveBeenCalled());
    const saved = mocks.create.mock.calls[0][0];
    expect(saved.harness).toBeUndefined();
    expect(saved.agents[0].capabilities).toBeUndefined();
    expect(mocks.capabilities).not.toHaveBeenCalled();
  });

  it("shows switches for opencode and persists role_type + capabilities", async () => {
    await proposeTeam();
    fireEvent.change(screen.getByLabelText("Agent harness"), { target: { value: "opencode" } });
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

  it("shows the server's validation error under the switches", async () => {
    const { ApiError } = await import("../../api");
    await proposeTeam();
    fireEvent.change(screen.getByLabelText("Agent harness"), { target: { value: "opencode" } });
    const sandbox = await screen.findByLabelText("dev sandbox");
    mocks.resolve.mockRejectedValueOnce(new ApiError(422, "bad capability"));
    fireEvent.change(sandbox, { target: { value: "host_dev" } });
    expect(await screen.findByText("bad capability")).toBeTruthy();
  });
});
