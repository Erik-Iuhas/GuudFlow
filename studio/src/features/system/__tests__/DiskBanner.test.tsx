import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { DiskBanner } from "../DiskBanner";

const disk = (free: number, low: boolean) => ({ path: "/d", free_bytes: free, total_bytes: 250 * 1024 ** 3, warn_below_bytes: 5 * 1024 ** 3, low });

describe("DiskBanner", () => {
  it("stays hidden while there is room", async () => {
    render(<DiskBanner load={() => Promise.resolve(disk(100 * 1024 ** 3, false))} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId("disk-banner")).toBeNull();
  });

  it("warns when low and prunes on request", async () => {
    const prune = vi.fn().mockResolvedValue({ steps: ["build cache: Total reclaimed space: 8.8GB"], freed_bytes: 8.8 * 1024 ** 3, disk: disk(9 * 1024 ** 3, false) });
    render(<DiskBanner load={() => Promise.resolve(disk(0.2 * 1024 ** 3, true))} prune={prune} />);
    expect(await screen.findByText(/Disk space is low: 0.2 GB free/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Prune Docker build cache" }));
    expect(await screen.findByText(/Freed 8.8 GB/)).toBeTruthy();
    expect(prune).toHaveBeenCalledOnce();
  });
});
