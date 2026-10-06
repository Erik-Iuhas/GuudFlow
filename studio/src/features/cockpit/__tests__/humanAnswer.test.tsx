import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HumanAnswerBox } from "../HumanAnswerBox";
import { LimitsControl } from "../LimitsControl";

describe("HumanAnswerBox", () => {
  it("sends the trimmed answer and clears the box", async () => {
    const onSubmit = vi.fn(async () => ({ ok: true }));
    render(<HumanAnswerBox runId="r1" onSubmit={onSubmit} />);
    const send = screen.getByRole("button", { name: "Send answer" });
    expect(send).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "  keep going  " } });
    fireEvent.click(send);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith("keep going"));
    await waitFor(() => expect(screen.getByLabelText("Your answer")).toHaveValue(""));
  });

  it("shows the error and keeps the text when sending fails", async () => {
    const onSubmit = vi.fn(async () => { throw new Error("boom"); });
    render(<HumanAnswerBox runId="r1" onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "yes" } });
    fireEvent.click(screen.getByRole("button", { name: "Send answer" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.getByLabelText("Your answer")).toHaveValue("yes");
  });
});

describe("LimitsControl", () => {
  it("raises the budget and adds minutes", async () => {
    const onSubmit = vi.fn(async () => ({ ok: true }));
    render(<LimitsControl runId="r1" budgetUsd={5} onSubmit={onSubmit} />);
    fireEvent.change(screen.getByLabelText("New budget in USD"), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "Raise budget" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ budget_usd: 12 }));
    fireEvent.click(screen.getByRole("button", { name: "+15 min" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ extend_minutes: 15 }));
  });
});
