import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConfirmProvider, useConfirm } from "../ConfirmProvider";

function Asker({ onResult }: { onResult: (ok: boolean) => void }) {
  const confirm = useConfirm();
  return <button onClick={() => void confirm({ title: "Stop this run?", confirmLabel: "Stop", destructive: true }).then(onResult)}>ask</button>;
}

describe("ConfirmProvider", () => {
  it("asks in the app, never the browser, and resolves with the choice", async () => {
    const native = vi.spyOn(window, "confirm");
    const onResult = vi.fn();
    render(<ConfirmProvider><Asker onResult={onResult} /></ConfirmProvider>);
    fireEvent.click(screen.getByRole("button", { name: "ask" }));
    expect(await screen.findByTestId("confirm-dialog")).toHaveTextContent("Stop this run?");
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(onResult).toHaveBeenCalledWith(true));
    fireEvent.click(screen.getByRole("button", { name: "ask" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(onResult).toHaveBeenLastCalledWith(false));
    expect(native).not.toHaveBeenCalled();
  });
});
