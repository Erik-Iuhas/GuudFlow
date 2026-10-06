import { describe, expect, it } from "vitest";
import {
  NO_TASK,
  artifactUrl,
  buildItems,
  filterItems,
  formatBytes,
  orderRounds,
} from "../model";
import { artifactSignal, makeStore, screenshotSignal } from "./helpers";

function items(signals: ReturnType<typeof artifactSignal>[]) {
  return buildItems(makeStore(signals).getState().artifacts, "run-1");
}

describe("buildItems", () => {
  it("sorts newest first and breaks ties by arrival", () => {
    const out = items([
      artifactSignal({ id: "a", path: "a.png", at: "2026-05-01T10:00:00+00:00" }),
      artifactSignal({ id: "b", path: "b.png", at: "2026-05-01T12:00:00+00:00" }),
      artifactSignal({ id: "c", path: "c.png", at: "2026-05-01T11:00:00+00:00" }),
      artifactSignal({ id: "d", path: "d.png", at: "2026-05-01T11:00:00+00:00" }),
    ]);
    expect(out.map((i) => i.id)).toEqual(["b", "d", "c", "a"]);
  });

  it("maps kinds and builds served URLs from served_path", () => {
    const out = items([
      artifactSignal({ id: "s", path: "d/flow.svg", kind: "svg", task: "T1" }),
      artifactSignal({ id: "h", path: "page.html", kind: "html" }),
      artifactSignal({ id: "p", path: "doc.pdf", kind: "pdf" }),
    ]);
    const byId = Object.fromEntries(out.map((i) => [i.id, i]));
    expect(byId.s.kind).toBe("svg");
    expect(byId.h.kind).toBe("html");
    expect(byId.p.kind).toBe("pdf");
    expect(byId.s.url).toBe("/api/runs/run-1/artifact-files/artifacts/frontend/d/flow.svg");
  });

  it("merges a screenshot with its ARTIFACT twin, keeping the card and the request id", () => {
    const store = makeStore([
      artifactSignal({ id: "art", path: ".guudflow/screens/20260501-1.png", task: "T7", size: 5000 }),
      screenshotSignal("shot", "frontend", "screenshots/frontend/20260501-1.png", { request_id: "rq9", question: "ok?" }),
      screenshotSignal("lone", "frontend", "screenshots/frontend/other.png"),
    ]);
    const out = buildItems(store.getState().artifacts, "run-1");
    expect(out).toHaveLength(2);
    const merged = out.find((i) => i.id === "art");
    expect(merged).toMatchObject({ kind: "screenshot", taskId: "T7", size: 5000, requestId: "rq9", question: "ok?" });
    expect(out.find((i) => i.id === "lone")?.url).toBe(
      "/api/runs/run-1/artifact-files/screenshots/frontend/other.png",
    );
  });

  it("escapes path segments", () => {
    expect(artifactUrl("r 1", "artifacts/a b/c#d.png")).toBe("/api/runs/r%201/artifact-files/artifacts/a%20b/c%23d.png");
  });
});

describe("filterItems", () => {
  const all = items([
    artifactSignal({ id: "1", path: "1.png", role: "frontend", task: "T1" }),
    artifactSignal({ id: "2", path: "2.png", role: "backend", task: "T1" }),
    artifactSignal({ id: "3", path: "3.png", role: "frontend", task: "T2" }),
    artifactSignal({ id: "4", path: "4.png", role: "frontend", task: null }),
  ]);
  const ids = (xs: typeof all) => xs.map((i) => i.id).sort();

  it("filters by task, role, both, and no task", () => {
    expect(ids(filterItems(all, { task: null, role: null }))).toEqual(["1", "2", "3", "4"]);
    expect(ids(filterItems(all, { task: "T1", role: null }))).toEqual(["1", "2"]);
    expect(ids(filterItems(all, { task: null, role: "frontend" }))).toEqual(["1", "3", "4"]);
    expect(ids(filterItems(all, { task: "T1", role: "frontend" }))).toEqual(["1"]);
    expect(ids(filterItems(all, { task: NO_TASK, role: null }))).toEqual(["4"]);
  });
});

describe("orderRounds", () => {
  it("orders one card's images oldest first and ignores other cards and non-images", () => {
    const all = items([
      artifactSignal({ id: "r3", path: "c.png", task: "T1", at: "2026-05-01T12:00:00+00:00" }),
      artifactSignal({ id: "r1", path: "a.png", task: "T1", at: "2026-05-01T10:00:00+00:00" }),
      artifactSignal({ id: "x", path: "x.png", task: "T2", at: "2026-05-01T11:00:00+00:00" }),
      artifactSignal({ id: "doc", path: "d.pdf", kind: "pdf", task: "T1", at: "2026-05-01T11:00:00+00:00" }),
      artifactSignal({ id: "r2", path: "b.png", task: "T1", at: "2026-05-01T11:30:00+00:00" }),
    ]);
    expect(orderRounds(all, "T1").map((i) => i.id)).toEqual(["r1", "r2", "r3"]);
  });
});

describe("formatBytes", () => {
  it("formats sizes", () => {
    expect(formatBytes(null)).toBe("unknown size");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});
