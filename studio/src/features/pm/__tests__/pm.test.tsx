import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { DEFAULT_PM_NAME, resetPmName, usePmName } from "../api";
import { SeatsPanel } from "../../team/SeatsPanel";
import { Conversation } from "../../plan/Conversation";
import type { ProjectConfig } from "../../../types";

function Name() {
  return <span data-testid="name">{usePmName()}</span>;
}

function mockPm(json: unknown, ok = true): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => ({ ok, status: ok ? 200 : 500, statusText: "x", json: async () => json }) as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => resetPmName());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("usePmName", () => {
  it("starts as Jack and shows the persona's profile name once fetched", async () => {
    const fetchMock = mockPm({ persona_id: "jack-project-manager-v1", name: "Pat" });
    render(<Name />);
    expect(screen.getByTestId("name")).toHaveTextContent(DEFAULT_PM_NAME);
    await waitFor(() => expect(screen.getByTestId("name")).toHaveTextContent("Pat"));
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/pm");
  });

  it("caches the name: a second component does not fetch again", async () => {
    const fetchMock = mockPm({ name: "Pat" });
    const first = render(<Name />);
    await waitFor(() => expect(screen.getByTestId("name")).toHaveTextContent("Pat"));
    first.unmount();
    render(<Name />);
    expect(screen.getByTestId("name")).toHaveTextContent("Pat");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("falls back to Jack when the request fails or the name is empty", async () => {
    mockPm({}, false);
    render(<Name />);
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    expect(screen.getByTestId("name")).toHaveTextContent("Jack");
    cleanup();
    resetPmName();
    mockPm({ name: "  " });
    render(<Name />);
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    expect(screen.getByTestId("name")).toHaveTextContent("Jack");
  });
});

describe("names in the screens", () => {
  it("renders the renamed manager in the seats panel and the conversation", async () => {
    mockPm({ name: "Pat" });
    const project = { project_id: "p1", goal: "g", seats: { lead: false, reviewer: "off" } } as unknown as ProjectConfig;
    render(
      <>
        <SeatsPanel project={project} onSaved={() => undefined} />
        <Conversation
          messages={[{ id: 1, at: 0, author: "pm", text: "Hello", draft_id: null, mode: "update" }]}
          pending={false}
          onSend={async () => undefined}
          onStart={async () => undefined}
        />
      </>,
    );
    expect(await screen.findByText("Empty: Pat decides and records why")).toBeInTheDocument();
    expect(screen.getByText("P")).toBeInTheDocument();
    expect(screen.getByText("project manager")).toBeInTheDocument();
  });
});
