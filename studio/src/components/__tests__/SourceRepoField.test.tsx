import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SourceRepoField } from "../SourceRepoField";

const noopHandlers = {
  onImportGit: vi.fn(async () => ({ findings: null, ok: true })),
  onImportLocalPath: vi.fn(async () => ({ findings: null, ok: true })),
  onImportLocalPathInPlace: vi.fn(async () => ({ findings: null, ok: true })),
};

function selectLocalPath() {
  // The "Local path" radio sits next to "Git URL" in the editor.
  fireEvent.click(screen.getByLabelText(/Local path/i));
}

describe("SourceRepoField — In-place sub-mode locked to Storage root", () => {
  it("disables In-place card when storage mode is global", () => {
    render(
      <SourceRepoField
        current={null}
        storageMode="global"
        storageBase=""
        {...noopHandlers}
      />,
    );
    selectLocalPath();
    const inPlace = screen.getByTestId("source-local-submode-in-place");
    expect(inPlace).toBeDisabled();
    // Hint surfaces the action: set Storage root in Settings.
    expect(inPlace.textContent).toMatch(/Set Storage root in Settings/i);
  });

  it("renders the Storage-root chip (no path input) when In-place is selected", () => {
    render(
      <SourceRepoField
        current={null}
        storageMode="in-directory"
        storageBase="X:/repo"
        {...noopHandlers}
      />,
    );
    selectLocalPath();
    fireEvent.click(screen.getByTestId("source-local-submode-in-place"));

    const chip = screen.getByTestId("source-in-place-chip");
    expect(chip.textContent).toContain("X:/repo");
    // Editable path input must be hidden in this sub-mode.
    expect(screen.queryByPlaceholderText("/abs/path/to/dir")).toBeNull();
  });

  it("Import in In-place mode posts storageBase as the path", async () => {
    const onImportLocalPathInPlace = vi.fn(async () => ({
      findings: null, ok: true,
    }));
    render(
      <SourceRepoField
        current={null}
        storageMode="in-directory"
        storageBase="X:/repo"
        {...noopHandlers}
        onImportLocalPathInPlace={onImportLocalPathInPlace}
      />,
    );
    selectLocalPath();
    fireEvent.click(screen.getByTestId("source-local-submode-in-place"));
    fireEvent.click(screen.getByRole("button", { name: /^Import$/ }));

    await waitFor(() => expect(onImportLocalPathInPlace).toHaveBeenCalledTimes(1));
    expect(onImportLocalPathInPlace).toHaveBeenCalledWith({ path: "X:/repo" });
  });

  it("Isolated sub-mode keeps the editable path input", () => {
    render(
      <SourceRepoField
        current={null}
        storageMode="in-directory"
        storageBase="X:/repo"
        {...noopHandlers}
      />,
    );
    selectLocalPath();
    // Default sub-mode is isolated.
    expect(screen.getByPlaceholderText("/abs/path/to/dir")).toBeInTheDocument();
    // Chip is not present.
    expect(screen.queryByTestId("source-in-place-chip")).toBeNull();
  });
});
