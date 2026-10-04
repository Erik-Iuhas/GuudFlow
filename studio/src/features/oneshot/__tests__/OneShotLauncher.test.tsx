import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { OneShotLauncher, validateOneShot } from "../OneShotLauncher";

const mocks = vi.hoisted(() => ({ start: vi.fn(), invoke: vi.fn(), examples: vi.fn() }));
vi.mock("../../../api", () => {
  class ApiError extends Error {
    constructor(public status: number, public detail: string) {
      super(`${status}: ${detail}`);
    }
  }
  return {
    ApiError,
    oneshotApi: { start: mocks.start },
    invokeApi: { invoke: mocks.invoke },
    exampleProjectsApi: { list: mocks.examples },
  };
});

function renderLauncher() {
  render(
    <MemoryRouter initialEntries={["/oneshot"]}>
      <Routes>
        <Route path="/oneshot" element={<OneShotLauncher />} />
        <Route path="/runs/:runId" element={<div>run view</div>} />
        <Route path="/projects/:id" element={<div>project page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}
const goalBox = () => screen.getByLabelText("Goal");

describe("validateOneShot", () => {
  it("requires a goal and a budget in (0, 1000]", () => {
    expect(validateOneShot("", "5").goal).toBeDefined();
    expect(validateOneShot("x", "0").budget).toBeDefined();
    expect(validateOneShot("x", "1001").budget).toBeDefined();
    expect(validateOneShot("x", "abc").budget).toBeDefined();
    expect(validateOneShot("x", "1000")).toEqual({});
  });
});

describe("OneShotLauncher", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.examples.mockResolvedValue([
      { id: "cli", title: "CLI tool", summary: "s", default_goal: "Build a CLI" },
    ]);
  });

  it("shows validation errors and does not post", async () => {
    renderLauncher();
    fireEvent.click(screen.getByRole("button", { name: "Launch" }));
    expect(await screen.findByText("Describe what you want done.")).toBeInTheDocument();
    expect(mocks.start).not.toHaveBeenCalled();
  });

  it("prefills the goal from an example card", async () => {
    renderLauncher();
    fireEvent.click(await screen.findByTestId("example-cli"));
    expect(goalBox()).toHaveValue("Build a CLI");
  });

  it("posts the payload and navigates to the run on 202 accepted", async () => {
    mocks.start.mockResolvedValue({ accepted: true, needs_approval: false, project_id: "p", run_id: "run_1" });
    renderLauncher();
    fireEvent.change(goalBox(), { target: { value: "  Build it  " } });
    fireEvent.click(screen.getByRole("switch", { name: "Web research" }));
    fireEvent.change(screen.getByLabelText(/Budget/), { target: { value: "12.5" } });
    fireEvent.click(screen.getByRole("switch", { name: "Demo mode" }));
    fireEvent.click(screen.getByRole("button", { name: "Launch" }));
    expect(await screen.findByText("run view")).toBeInTheDocument();
    expect(mocks.start).toHaveBeenCalledWith({ goal: "Build it", web: "off", budget: 12.5, demo: true });
  });

  it("shows the needs-approval state and starts after approving", async () => {
    mocks.start.mockResolvedValue({
      accepted: false, needs_approval: true, project_id: "oneshot-ab",
      estimated_cost: { total_usd: 1.5, by_role: {}, notes: [] }, message: "Needs approval.",
    });
    mocks.invoke.mockResolvedValue({ accepted: true, run_id: "run_9", job_id: "j", project_id: "oneshot-ab" });
    renderLauncher();
    fireEvent.change(goalBox(), { target: { value: "Build it" } });
    fireEvent.click(screen.getByRole("button", { name: "Launch" }));
    expect(await screen.findByTestId("needs-approval")).toHaveTextContent("$1.50");
    expect(screen.getByRole("link", { name: "Review the team" })).toHaveAttribute("href", "/projects/oneshot-ab");
    fireEvent.click(screen.getByRole("button", { name: "Approve and start" }));
    await waitFor(() => expect(screen.getByText("run view")).toBeInTheDocument());
    expect(mocks.invoke).toHaveBeenCalledWith("oneshot-ab", { confirm: true });
  });

  it("shows server errors", async () => {
    const { ApiError } = await import("../../../api");
    mocks.start.mockRejectedValue(new ApiError(400, "budget too high"));
    renderLauncher();
    fireEvent.change(goalBox(), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Launch" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("budget too high");
  });
});
