import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { PersonaSlideOver } from "../PersonaSlideOver";
import { WorkerCard } from "../WorkerCard";

vi.mock("../../api", () => ({
  personasApi: {
    get: vi.fn(),
    episodes: vi.fn().mockResolvedValue([]),
  },
  ApiError: class ApiError extends Error {
    constructor(public status: number, public detail: string) {
      super(detail);
    }
  },
}));

import { personasApi } from "../../api";
const mockGet = personasApi.get as ReturnType<typeof vi.fn>;

const PERSONA = {
  persona_id: "mike-01",
  name: "Mike",
  specialty: "backend developer",
  character: "methodical",
  goal: "Build robust APIs",
  n_episodes: 4,
  reputation: {
    production: { total_runs: 3, reviewer_first_pass_rate: 0.75 },
    training: { total_runs: 1, graduation_status: "ungraduated" },
  },
};

describe("PersonaSlideOver", () => {
  beforeEach(() => {
    mockGet.mockResolvedValue(PERSONA);
  });

  it("renders persona name and reputation snapshot", async () => {
    render(<PersonaSlideOver personaId="mike-01" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("Mike")).toBeInTheDocument());
    expect(screen.getByText("Build robust APIs")).toBeInTheDocument();
    // total runs = 3 prod + 1 training = 4; first-pass rate 75%
    expect(screen.getByText("75%")).toBeInTheDocument();
    // n_episodes shown in reputation row
    const totalRunsEl = screen.getAllByText("4");
    expect(totalRunsEl.length).toBeGreaterThan(0);
  });

  it("closes via the close button", async () => {
    const onClose = vi.fn();
    render(<PersonaSlideOver personaId="mike-01" onClose={onClose} />);
    await waitFor(() => expect(screen.getByText("Mike")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("closes on Escape key", async () => {
    const onClose = vi.fn();
    render(<PersonaSlideOver personaId="mike-01" onClose={onClose} />);
    await waitFor(() => expect(screen.getByText("Mike")).toBeInTheDocument());
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("empty goal field renders empty state, not crash", async () => {
    mockGet.mockResolvedValue({ ...PERSONA, goal: "" });
    render(<PersonaSlideOver personaId="mike-01" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("No goal set.")).toBeInTheDocument());
  });

  it("WorkerCard roster mode click fires onOpenPersona", () => {
    const onOpenPersona = vi.fn();
    render(
      <WorkerCard
        agent={{ role: "developer", persona_id: "mike-01" }}
        mode="roster"
        onOpenPersona={onOpenPersona}
      />,
    );
    const card = document.querySelector(".worker-card--roster")!;
    fireEvent.click(card);
    expect(onOpenPersona).toHaveBeenCalledWith("mike-01");
  });
});
