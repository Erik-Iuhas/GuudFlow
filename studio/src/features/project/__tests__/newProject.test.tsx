import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { slugify } from "../newProjectModel";

const mocks = vi.hoisted(() => ({
  propose: vi.fn(),
  create: vi.fn(),
  upload: vi.fn(),
  importGit: vi.fn(),
}));

vi.mock("../../../api", () => {
  class ApiError extends Error {
    constructor(public status: number, public detail: string) {
      super(`${status}: ${detail}`);
    }
  }
  return {
    ApiError,
    getToken: () => null,
    metaApi: { propose: mocks.propose },
    projectsApi: { create: mocks.create },
    inputsApi: { uploadFiles: mocks.upload, importGit: mocks.importGit },
  };
});

import { NewProject } from "../NewProject";

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search}</div>;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/projects/new"]}>
        <Routes>
          <Route path="/projects/new" element={<NewProject />} />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const PROPOSAL = { rationale: "r", agents: [{ role: "dev", model_tier: "solid" }], rbac: { dev: { allowed_scopes: [] } } };

beforeEach(() => {
  vi.resetAllMocks();
  mocks.propose.mockResolvedValue(PROPOSAL);
  mocks.create.mockResolvedValue({});
});

describe("slugify", () => {
  it("makes a short lowercase slug", () => {
    expect(slugify("A Book-Club app!  ")).toBe("a-book-club-app");
    expect(slugify("x".repeat(60)).length).toBe(40);
  });
});

describe("NewProject", () => {
  it("shows the seats and the five start modes as pressable cards", () => {
    renderPage();
    const owner = screen.getByRole("button", { name: /Owner/ }) as HTMLButtonElement;
    expect(owner.disabled).toBe(true);
    expect(owner.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: /Lead/ }).getAttribute("aria-pressed")).toBe("false");
    for (const t of ["Spec first", "Prototype first", "Test first", "Research first", "Maintain"]) {
      expect(screen.getByRole("button", { name: new RegExp(t) })).toBeTruthy();
    }
    expect(screen.getByRole("button", { name: /Spec first/ }).getAttribute("aria-pressed")).toBe("true");
    expect((screen.getByLabelText(/Spend cap/) as HTMLInputElement).value).toBe("5");
  });

  it("derives an editable name from the description and disables Start until there is one", () => {
    renderPage();
    const start = screen.getByRole("button", { name: "Start planning" }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("What are you making?"), { target: { value: "A tiny Book Club app" } });
    const name = screen.getByLabelText("Project name") as HTMLInputElement;
    expect(name.value).toBe("a-tiny-book-club-app");
    expect(start.disabled).toBe(false);
    fireEvent.change(name, { target: { value: "bookclub" } });
    fireEvent.change(screen.getByLabelText("What are you making?"), { target: { value: "Something else" } });
    expect(name.value).toBe("bookclub");
  });

  it("proposes with the start mode, creates the project with seats and cap, then opens Plan", async () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("What are you making?"), { target: { value: "A tiny Book Club app" } });
    fireEvent.click(screen.getByRole("button", { name: /Lead/ }));
    fireEvent.click(screen.getByRole("button", { name: /Reviewer/ }));
    fireEvent.click(screen.getByRole("button", { name: /Test first/ }));
    expect(screen.getByRole("button", { name: /Test first/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: /Spec first/ }).getAttribute("aria-pressed")).toBe("false");
    fireEvent.change(screen.getByLabelText(/Spend cap/), { target: { value: "12.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Start planning" }));

    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/projects/a-tiny-book-club-app?tab=plan"));
    expect(mocks.propose).toHaveBeenCalledWith("A tiny Book Club app", { startMode: "test_first", kind: "software_app" });
    const body = mocks.create.mock.calls[0][0];
    expect(body.project_id).toBe("a-tiny-book-club-app");
    expect(body.goal).toBe("A tiny Book Club app");
    expect(body.agents).toEqual(PROPOSAL.agents);
    expect(body.seats).toEqual({ lead: true, reviewer: "all" });
    expect(body.start_mode).toBe("test_first");
    expect(body.approval_gates.token_budget_usd).toBe(12.5);
    expect(body.harness).toBe("opencode");
  });

  it("defaults to no lead, no reviewer and a $5 cap", async () => {
    renderPage();
    fireEvent.change(screen.getByLabelText("What are you making?"), { target: { value: "x app" } });
    fireEvent.click(screen.getByRole("button", { name: "Start planning" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalled());
    const body = mocks.create.mock.calls[0][0];
    expect(body.seats).toEqual({ lead: false, reviewer: "off" });
    expect(body.start_mode).toBe("spec_first");
    expect(body.approval_gates.token_budget_usd).toBe(5);
  });

  it("shows progress while the team is drafted", async () => {
    let release: (v: unknown) => void = () => {};
    mocks.propose.mockReturnValue(new Promise((r) => (release = r)));
    renderPage();
    fireEvent.change(screen.getByLabelText("What are you making?"), { target: { value: "x app" } });
    fireEvent.click(screen.getByRole("button", { name: "Start planning" }));
    expect((await screen.findByRole("status")).textContent).toMatch(/Drafting the team/);
    expect((screen.getByRole("button", { name: "Working…" }) as HTMLButtonElement).disabled).toBe(true);
    release(PROPOSAL);
    await waitFor(() => expect(mocks.create).toHaveBeenCalled());
  });

  it("shows an error and stays on the page when the proposal fails", async () => {
    mocks.propose.mockRejectedValue(new Error("no key"));
    renderPage();
    fireEvent.change(screen.getByLabelText("What are you making?"), { target: { value: "x app" } });
    fireEvent.click(screen.getByRole("button", { name: "Start planning" }));
    expect(await screen.findByText("no key")).toBeTruthy();
    expect(mocks.create).not.toHaveBeenCalled();
    expect(screen.queryByTestId("where")).toBeNull();
    expect((screen.getByRole("button", { name: "Start planning" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("uploads attached files and imports the repo after the project exists", async () => {
    mocks.upload.mockResolvedValue({});
    mocks.importGit.mockResolvedValue({});
    renderPage();
    fireEvent.change(screen.getByLabelText("What are you making?"), { target: { value: "x app" } });
    const file = new File(["hi"], "notes.md");
    fireEvent.change(screen.getByLabelText("Files to attach"), { target: { files: [file] } });
    expect(screen.getByText("notes.md")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Use an existing repo" }));
    fireEvent.change(screen.getByLabelText("Git URL of the repo"), { target: { value: "https://example.com/r.git" } });
    fireEvent.click(screen.getByRole("button", { name: "Start planning" }));
    await waitFor(() => expect(screen.getByTestId("where")).toBeTruthy());
    expect(mocks.upload).toHaveBeenCalledWith("x-app", [file]);
    expect(mocks.importGit).toHaveBeenCalledWith("x-app", expect.objectContaining({ git_url: "https://example.com/r.git" }));
  });
});
