import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AnnotateImage } from "../AnnotateImage";
import { formatRegionText, regionFromPoints, submitAnnotation } from "../annotate";
import type { GalleryItem } from "../model";

const ITEM: GalleryItem = {
  id: "s1", name: "shot.png", path: "screenshots/frontend/shot.png", kind: "screenshot", role: "frontend",
  taskId: "T1", size: 100, createdAt: "", ts: 0, order: 0,
  url: "/api/runs/run-1/artifact-files/screenshots/frontend/shot.png", requestId: "rq1", question: "Is the header right?",
};

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn(async () => new Response("{}", { status: 201, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  localStorage.setItem("guudflow.token", "tok");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

function calls(): { url: string; body: unknown; auth: string | undefined }[] {
  return fetchMock.mock.calls.map(([url, init]) => ({
    url: String(url),
    body: JSON.parse(String((init as RequestInit).body)),
    auth: ((init as RequestInit).headers as Record<string, string>).Authorization,
  }));
}

describe("annotation helpers", () => {
  it("encodes the region in the text", () => {
    expect(formatRegionText({ x: 1, y: 2, w: 3, h: 4 }, " too wide ")).toBe("[region 1,2,3,4] too wide");
    expect(formatRegionText(null, "hello")).toBe("hello");
  });

  it("normalizes a drag in any direction", () => {
    expect(regionFromPoints({ x: 60, y: 40 }, { x: 10.4, y: 10.6 })).toEqual({ x: 10, y: 11, w: 50, h: 29 });
  });

  it("falls back to a run comment for an artifact with no card or request", async () => {
    const result = await submitAnnotation("run-1", { taskId: null, requestId: null }, null, "looks off");
    expect(result.sent).toEqual(["run comment"]);
    expect(calls()).toEqual([{ url: "/api/runs/run-1/comment", body: { text: "looks off", priority: "normal" }, auth: "Bearer tok" }]);
  });

  it("refuses an empty note", async () => {
    await expect(submitAnnotation("run-1", { taskId: "T1", requestId: null }, null, "  ")).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("AnnotateImage", () => {
  function drawRegion() {
    const img = screen.getByAltText("Annotate shot.png") as HTMLImageElement;
    // Shown at 200x100 CSS px but 400 px wide: image px = screen px x 2.
    Object.defineProperty(img, "naturalWidth", { value: 400 });
    img.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 100, right: 200, bottom: 100, x: 0, y: 0, toJSON: () => ({}) });
    const surface = screen.getByTestId("annotate-surface");
    fireEvent.pointerDown(surface, { clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 60, clientY: 40, pointerId: 1 });
    fireEvent.pointerUp(surface, { clientX: 60, clientY: 40, pointerId: 1 });
  }

  it("draws a rectangle and posts the card comment and the visual feedback", async () => {
    render(<AnnotateImage runId="run-1" item={ITEM} />);
    expect(screen.getByText("The agent asks: Is the header right?")).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "Send note" });
    expect(send).toBeDisabled();

    drawRegion();
    expect(screen.getByTestId("annotate-rect")).toBeInTheDocument();
    expect(screen.getByTestId("annotate-region")).toHaveTextContent("Region 20,20 100x60 px");

    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "make the header taller" } });
    expect(send).toBeEnabled();
    fireEvent.click(send);

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Sent as card comment and visual feedback."));
    const text = "[region 20,20,100,60] make the header taller";
    expect(calls()).toEqual([
      { url: "/api/runs/run-1/cards/T1/comments", body: { text }, auth: "Bearer tok" },
      { url: "/api/runs/run-1/visual_feedback", body: { request_id: "rq1", feedback: text }, auth: "Bearer tok" },
    ]);
  });

  it("posts only the card comment when no agent is waiting", async () => {
    render(<AnnotateImage runId="run-1" item={{ ...ITEM, requestId: null, question: null }} />);
    drawRegion();
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Send note" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(calls()[0].url).toBe("/api/runs/run-1/cards/T1/comments");
  });

  it("shows the server's error", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "run 'run-1' is not active" }), { status: 404, headers: { "content-type": "application/json" } }));
    render(<AnnotateImage runId="run-1" item={{ ...ITEM, requestId: null }} />);
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: "Send note" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("not active"));
  });
});
