import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { CodeBrowser } from "../CodeBrowser";
import { buildTree, parentsOf } from "../model";

const DIFF = [
  "diff --git a/web/src/main.js b/web/src/main.js",
  "--- a/web/src/main.js",
  "+++ b/web/src/main.js",
  "@@ -1 +1,2 @@",
  " a",
  "+copy()",
  "",
].join("\n");

function respond(url: string): unknown {
  if (url.includes("/code/roles")) return { roles: ["developer"] };
  if (url.includes("/code/tree")) {
    const role = url.includes("role=developer");
    return {
      source: role ? "role" : url.includes("/projects/") ? "project" : "run",
      base: null, truncated: false,
      entries: [
        { path: "README.md", status: "", added: 0, removed: 0 },
        { path: "web/src/main.js", status: role ? "M" : "", added: 1, removed: 0 },
      ],
      last_edit: role ? { path: "web/src/main.js", at: 1 } : undefined,
    };
  }
  if (url.includes("/code/file")) {
    const diff = url.includes("diff=1");
    return { path: "web/src/main.js", text: "a\ncopy()\n", size: 9, binary: false, too_large: false,
             deleted: false, diff: diff ? DIFF : null };
  }
  return {};
}

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

afterEach(() => vi.unstubAllGlobals());

describe("buildTree", () => {
  it("nests folders first and counts changes", () => {
    const root = buildTree([
      { path: "z.md", status: "", added: 0, removed: 0 },
      { path: "a/b.js", status: "A", added: 1, removed: 0 },
      { path: "a/c.js", status: "", added: 0, removed: 0 },
    ]);
    expect(root.children.map((c) => c.name)).toEqual(["a", "z.md"]);
    expect(root.children[0].changed).toBe(1);
    expect(buildTree(root.children[0].children.map((c) => c.entry!), true).children[0].children.map((c) => c.name))
      .toEqual(["b.js"]);
    expect(parentsOf("a/b/c.js")).toEqual(["a", "a/b"]);
  });
});

describe("CodeBrowser", () => {
  it("shows the project's files and opens one", async () => {
    const fetchMock = vi.fn(async (url: string) => new Response(JSON.stringify(respond(url))));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<CodeBrowser projectId="p1" />);
    fireEvent.click(await screen.findByRole("button", { name: /main\.js/ }));
    expect((await screen.findByTestId("code-file")).textContent).toContain("copy()");
    expect(screen.queryByRole("group", { name: "View" })).toBeNull();   // no diff for accepted main
    expect(fetchMock.mock.calls.some(([u]) => String(u).startsWith("/api/projects/p1/code/file?path=web%2Fsrc%2Fmain.js"))).toBe(true);
  });

  it("follows a working worker's last edit and shows its diff", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(respond(url)))));
    wrap(<CodeBrowser runId="r1" live names={{ developer: "Bram" }} working={["developer"]} />);
    const worker = await screen.findByRole("combobox", { name: "Worker" });
    await screen.findByRole("option", { name: "Bram · developer · working now" });
    fireEvent.change(worker, { target: { value: "developer" } });
    await screen.findByText("● editing");
    expect((await screen.findByTestId("code-file")).textContent).toContain("copy()");
    fireEvent.click(screen.getByRole("button", { name: "Diff" }));
    await waitFor(() => expect(screen.getByTestId("diff-viewer")).toBeTruthy());
  });

  it("opens a file asked for by a card", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(respond(url)))));
    wrap(<CodeBrowser runId="r1" focus={{ path: "web/src/main.js" }} />);
    await waitFor(() => expect(screen.getByTestId("diff-viewer")).toBeTruthy());
  });
});

describe("DependencyList", () => {
  it("lists shared packages with who added and approved them", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(
      url.endsWith("/deps")
        ? { environments: [{ manager: "npm", key: "npm-1", packages: [
            { name: "vite", version: "5.4.1", added_by: "developer", approved_by: "human", run_id: "run_a", at: 1 }] }] }
        : respond(url),
    ))));
    wrap(<CodeBrowser projectId="p1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Dependencies" }));
    const list = await screen.findByTestId("dependency-list");
    expect(list.textContent).toContain("vite");
    expect(list.textContent).toContain("by a person");
  });
});
