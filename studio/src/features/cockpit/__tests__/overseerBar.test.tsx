import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createRunStore } from "../../../stores/runEvents";
import { RunStoreProvider } from "../../../stores/runStoreContext";
import { OverseerBar } from "../OverseerBar";

afterEach(() => vi.unstubAllGlobals());

describe("OverseerBar", () => {
  it("wakes the overseer from an in-app form, never a browser dialog", async () => {
    const prompt = vi.fn();
    vi.stubGlobal("prompt", prompt);
    localStorage.setItem("guudflow.token", "tok");
    const fetch = vi.fn(async () => ({ ok: true, status: 200, headers: new Headers({ "content-type": "application/json" }),
      json: async () => ({ ok: true, signal_id: "w1" }) }) as unknown as Response);
    vi.stubGlobal("fetch", fetch);
    render(<RunStoreProvider store={createRunStore("r1")}><OverseerBar runId="r1" present active /></RunStoreProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Wake overseer…" }));
    fireEvent.change(screen.getByLabelText("Why wake the overseer"), { target: { value: "T3 looks stuck" } });
    fireEvent.click(screen.getByRole("button", { name: "Wake overseer" }));
    await waitFor(() => expect(screen.getByTestId("wake-state")).toBeInTheDocument());
    const calls = fetch.mock.calls as unknown as [string, RequestInit][];
    expect(calls[0][0]).toBe("/api/runs/r1/overseer/wake");
    expect(JSON.parse(String(calls[0][1].body))).toEqual({ text: "T3 looks stuck" });
    expect(prompt).not.toHaveBeenCalled();
  });
});
