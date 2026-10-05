import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { buildItems, orderRounds, visualReviewUrls } from "../model";
import { RoundCompare } from "../RoundCompare";
import { artifactSignal, makeStore } from "./helpers";

afterEach(cleanup);

function rounds() {
  const store = makeStore([
    artifactSignal({ id: "r3", path: "c.png", task: "T1", at: "2026-05-01T12:00:00+00:00" }),
    artifactSignal({ id: "r1", path: "a.png", task: "T1", at: "2026-05-01T10:00:00+00:00" }),
    artifactSignal({ id: "r2", path: "b.png", task: "T1", at: "2026-05-01T11:00:00+00:00" }),
  ]);
  return orderRounds(buildItems(store.getState().artifacts, "run-1"), "T1");
}

describe("RoundCompare", () => {
  it("lists rounds oldest first and defaults to the last two", () => {
    const urls = visualReviewUrls("run-1", "T1");
    render(<RoundCompare rounds={rounds()} diffUrl={urls.diff} baselineUrl={urls.baseline} />);
    const before = screen.getByLabelText("Before") as HTMLSelectElement;
    const labels = Array.from(before.options).map((o) => o.textContent ?? "");
    expect(labels).toHaveLength(3);
    expect(labels[0]).toMatch(/^Round 1 .*a\.png$/);
    expect(labels[1]).toMatch(/^Round 2 .*b\.png$/);
    expect(labels[2]).toMatch(/^Round 3 .*c\.png$/);

    expect(screen.getByTestId("compare-before").getAttribute("src")).toContain("/artifacts/frontend/b.png");
    expect(screen.getByTestId("compare-after").getAttribute("src")).toContain("/artifacts/frontend/c.png");
  });

  it("changes the compared rounds and the slider position", () => {
    const urls = visualReviewUrls("run-1", "T1");
    render(<RoundCompare rounds={rounds()} diffUrl={urls.diff} baselineUrl={urls.baseline} />);
    fireEvent.change(screen.getByLabelText("Before"), { target: { value: "r1" } });
    expect(screen.getByTestId("compare-before").getAttribute("src")).toContain("/a.png");

    const slider = screen.getByLabelText("Before and after position") as HTMLInputElement;
    expect(slider.value).toBe("50");
    fireEvent.change(slider, { target: { value: "20" } });
    const clip = screen.getByTestId("compare-before").parentElement as HTMLElement;
    expect(clip.style.clipPath).toBe("inset(0 80% 0 0)");
  });

  it("says so with nothing to compare", () => {
    const urls = visualReviewUrls("run-1", "T1");
    render(<RoundCompare rounds={[]} diffUrl={urls.diff} baselineUrl={urls.baseline} />);
    expect(screen.getByText("Nothing to compare yet.")).toBeInTheDocument();
  });

  it("points at the pre-check diff and baseline files of the card", () => {
    expect(visualReviewUrls("run-1", "T1")).toEqual({
      diff: "/api/runs/run-1/artifact-files/visual/T1/diff_1.png",
      baseline: "/api/runs/run-1/artifact-files/baselines/tasks/T1.png",
    });
  });
});
