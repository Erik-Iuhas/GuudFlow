import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PlanMessageWire } from "../api";
import { Conversation } from "../Conversation";

afterEach(cleanup);

const first: PlanMessageWire = { id: 1, at: 1_700_000_000, author: "pm", text: "Drafted.",
  draft_id: "draft-1", mode: "update" } as PlanMessageWire;

function setup(onSend: (text: string) => Promise<void>) {
  render(<QueryClientProvider client={new QueryClient()}>
    <Conversation messages={[first]} pending={false} onSend={(t) => onSend(t)} onStart={async () => {}}
      error={null} currentDraftId="draft-1" />
  </QueryClientProvider>);
  return screen.getByLabelText(/Answer, push back/) as HTMLTextAreaElement;
}

describe("sending to Jack (worker context F1)", () => {
  it("clears the box and shows the message at once, while Jack works", async () => {
    let finish: () => void = () => {};
    const box = setup(() => new Promise<void>((resolve) => { finish = resolve; }));
    fireEvent.change(box, { target: { value: "Use pygame, not TypeScript." } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(box.value).toBe("");
    expect(screen.getByTestId("outgoing")).toHaveTextContent("Use pygame, not TypeScript.");
    await act(async () => finish());
    await waitFor(() => expect(screen.queryByTestId("outgoing")).toBeNull());
  });

  it("gives the words back when sending fails", async () => {
    const box = setup(() => Promise.reject(new Error("502: the model failed")));
    fireEvent.change(box, { target: { value: "Add a level select." } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(box.value).toBe("Add a level select."));
    expect(screen.getByRole("alert")).toHaveTextContent("502: the model failed");
  });
});
