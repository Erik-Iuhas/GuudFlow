import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ArtifactGallery } from "../ArtifactGallery";
import { MALICIOUS_SVG } from "../testFixtures";
import { VIRTUALIZE_THRESHOLD } from "../model";
import { artifactSignal, makeStore, withStore } from "./helpers";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

function tileNames(): string[] {
  return screen.queryAllByTestId("artifact-tile").map((t) => within(t).getAllByText(/\.(png|svg|html|pdf)$/)[0].textContent ?? "");
}

const SAMPLE = [
  artifactSignal({ id: "1", path: "login.png", role: "frontend", task: "T1", at: "2026-05-01T10:00:00+00:00" }),
  artifactSignal({ id: "2", path: "api.svg", kind: "svg", role: "backend", task: "T2", at: "2026-05-01T12:00:00+00:00" }),
  artifactSignal({ id: "3", path: "home.png", role: "frontend", task: "T2", at: "2026-05-01T11:00:00+00:00" }),
  artifactSignal({ id: "4", path: "report.html", kind: "html", role: "backend", task: null, at: "2026-05-01T09:00:00+00:00" }),
];

describe("ArtifactGallery", () => {
  it("shows the empty state", () => {
    render(withStore(makeStore([]), <ArtifactGallery runId="run-1" />));
    expect(screen.getByText(/No artifacts yet/)).toBeInTheDocument();
  });

  it("lists artifacts newest first with kind and size", () => {
    render(withStore(makeStore(SAMPLE), <ArtifactGallery runId="run-1" />));
    expect(tileNames()).toEqual(["api.svg", "home.png", "login.png", "report.html"]);
    const first = screen.getAllByTestId("artifact-tile")[0];
    expect(within(first).getByText("SVG")).toBeInTheDocument();
    expect(within(first).getByText("2.0 KB")).toBeInTheDocument();
    expect(screen.getByTestId("artifact-count")).toHaveTextContent("4 artifacts");
  });

  it("filters by task and role", () => {
    render(withStore(makeStore(SAMPLE), <ArtifactGallery runId="run-1" />));
    fireEvent.change(screen.getByLabelText("Task"), { target: { value: "T2" } });
    expect(tileNames()).toEqual(["api.svg", "home.png"]);
    expect(screen.getByTestId("artifact-count")).toHaveTextContent("2 of 4 artifacts");

    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "frontend" } });
    expect(tileNames()).toEqual(["home.png"]);

    fireEvent.change(screen.getByLabelText("Task"), { target: { value: "" } });
    expect(tileNames()).toEqual(["home.png", "login.png"]);

    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Task"), { target: { value: "__none__" } });
    expect(tileNames()).toEqual(["report.html"]);
  });

  it("tells the user when filters match nothing", () => {
    render(withStore(makeStore(SAMPLE), <ArtifactGallery runId="run-1" />));
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "backend" } });
    fireEvent.change(screen.getByLabelText("Task"), { target: { value: "T1" } });
    expect(screen.getByText("Nothing matches these filters.")).toBeInTheDocument();
  });

  it("renders only part of a very long list", () => {
    // jsdom has no layout: give every element a size so the virtualizer has a viewport.
    for (const prop of ["offsetWidth", "clientWidth"]) {
      vi.spyOn(HTMLElement.prototype, prop as "offsetWidth", "get").mockReturnValue(900);
    }
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(600);
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(600);
    const many = Array.from({ length: VIRTUALIZE_THRESHOLD + 50 }, (_, i) =>
      artifactSignal({ id: `m${i}`, path: `shot-${i}.png`, at: new Date(2026, 4, 1, 0, 0, i).toISOString() }),
    );
    render(withStore(makeStore(many), <ArtifactGallery runId="run-1" />));
    expect(screen.getByTestId("artifact-virtual-scroller")).toBeInTheDocument();
    const rendered = screen.getAllByTestId("artifact-tile").length;
    expect(rendered).toBeGreaterThan(0);
    expect(rendered).toBeLessThan(VIRTUALIZE_THRESHOLD);
    expect(screen.getByTestId("artifact-count")).toHaveTextContent(`${many.length} artifacts`);
  });

  it("does not virtualize a short list", () => {
    render(withStore(makeStore(SAMPLE), <ArtifactGallery runId="run-1" />));
    expect(screen.queryByTestId("artifact-virtual-scroller")).toBeNull();
  });

  it("opens HTML in an iframe with an empty sandbox, no srcdoc and no referrer", () => {
    render(withStore(makeStore(SAMPLE), <ArtifactGallery runId="run-1" />));
    fireEvent.click(screen.getByRole("button", { name: /Open report\.html/ }));
    const frame = screen.getAllByTitle("report.html").find((el) => el.tagName === "IFRAME") as HTMLElement;
    expect(frame.tagName).toBe("IFRAME");
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(frame.hasAttribute("srcdoc")).toBe(false);
    expect(frame.getAttribute("src")).toBe("/api/runs/run-1/artifact-files/artifacts/backend/report.html");
  });

  describe("SVG viewer", () => {
    let blobs: Blob[];
    beforeEach(() => {
      blobs = [];
      vi.stubGlobal("fetch", vi.fn(async () => new Response(MALICIOUS_SVG, { status: 200 })));
      URL.createObjectURL = vi.fn((b: Blob | MediaSource) => {
        blobs.push(b as Blob);
        return `blob:test/${blobs.length}`;
      });
      URL.revokeObjectURL = vi.fn();
    });

    it("shows sanitized SVG through a blob URL in an <img>, never inline", async () => {
      render(withStore(makeStore(SAMPLE), <ArtifactGallery runId="run-1" />));
      fireEvent.click(screen.getByRole("button", { name: /Open api\.svg/ }));
      const img = await screen.findByAltText("api.svg");
      expect(img.tagName).toBe("IMG");
      expect(img.getAttribute("src")).toMatch(/^blob:/);
      expect(document.querySelector("dialog svg, [role=dialog] svg[viewBox='0 0 40 40']")).toBeNull();

      expect(blobs).toHaveLength(1);
      expect(blobs[0].type).toBe("image/svg+xml");
      const text = await readBlob(blobs[0]);
      expect(text).toContain("<circle");
      expect(text).not.toMatch(/<script|onload|onclick|foreignObject|evil\.test|javascript:/i);
      // Nothing from the file reached the page DOM.
      expect(document.body.innerHTML).not.toContain("evil.test");
    });

    it("says so when the file cannot be loaded", async () => {
      vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 404 })));
      render(withStore(makeStore(SAMPLE), <ArtifactGallery runId="run-1" />));
      fireEvent.click(screen.getByRole("button", { name: /Open api\.svg/ }));
      await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 404/));
    });
  });
});
