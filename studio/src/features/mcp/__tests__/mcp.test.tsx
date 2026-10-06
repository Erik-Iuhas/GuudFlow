import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { McpSettings } from "../McpSettings";
import { McpToolsField } from "../McpToolsField";
import type { McpServerWire } from "../api";
import { effectiveTools, inferRoleType, offeredTools, splitArgs, toggleGrant } from "../model";
import type { AgentConfig } from "../../../types";

const GITHUB: McpServerWire = {
  name: "github", transport: "http", command: "", args: [], env: [], url: "https://mcp.gh.test/mcp",
  headers: {}, credential: { env: "GITHUB_TOKEN" }, enabled: true,
  default_grants: { search_code: ["researcher", "builder"], create_issue: ["planner"] },
  tools: [
    { name: "search_code", description: "Search code" },
    { name: "create_issue", description: "Open an issue" },
  ],
  key: "env",
};
const FS: McpServerWire = {
  name: "files", transport: "stdio", command: "npx", args: ["-y", "fs-server"], env: [], url: "",
  headers: {}, credential: {}, enabled: true, default_grants: {}, tools: [], key: "none",
};
const PAID: McpServerWire = { ...FS, name: "paid", command: "paid-mcp", credential: { env: "PAID_KEY" }, key: "missing" };
const OFF: McpServerWire = { ...FS, name: "dormant", enabled: false };

describe("model helpers", () => {
  it("infers role types like the backend and resolves effective tools", () => {
    expect(inferRoleType({ role: "Code Reviewer" })).toBe("reviewer");
    expect(inferRoleType({ role: "dev", is_reviewer: true })).toBe("reviewer");
    expect(inferRoleType({ role: "ui builder" })).toBe("frontend_builder");
    expect(inferRoleType({ role: "builder" })).toBe("builder");
    expect(inferRoleType({ role: "x", agent_class_role_type: "planner" })).toBe("planner");
    const offers = offeredTools([GITHUB, { ...GITHUB, name: "off", enabled: false }]);
    expect(effectiveTools(null, "builder", offers)).toEqual(["github__search_code"]);
    expect(effectiveTools([], "builder", offers)).toEqual([]);
    expect(effectiveTools(["github__create_issue", "gone__x"], "builder", offers)).toEqual(["github__create_issue"]);
  });

  it("splits args and toggles grants", () => {
    expect(splitArgs('-y "a b" c')).toEqual(["-y", "a b", "c"]);
    expect(toggleGrant({}, "t", "builder", true)).toEqual({ t: ["builder"] });
    expect(toggleGrant({ t: ["builder"] }, "t", "builder", false)).toEqual({});
  });
});

describe("McpSettings", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let servers: McpServerWire[];
  const calls = () => fetchMock.mock.calls.map(([url, init]) => ({
    url: String(url), method: (init as RequestInit | undefined)?.method ?? "GET",
    body: (init as RequestInit | undefined)?.body ? JSON.parse(String((init as RequestInit).body)) : undefined,
  }));
  const json = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status });

  beforeEach(() => {
    servers = structuredClone([GITHUB, FS, PAID, OFF]);
    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      const m = /^\/api\/mcp\/servers\/([^/]+)(\/key|\/test)?$/.exec(url);
      if (url === "/api/mcp/servers" && method === "GET") return json({ servers });
      if (url === "/api/mcp/servers" && method === "POST") {
        const s = { ...body, tools: [], key: body.credential.env ? "env" : "none" };
        servers.push(s);
        return json(s, 201);
      }
      if (url.startsWith("/api/mcp/servers/import")) {
        servers.push({ ...FS, name: "imported" });
        return json({ imported: ["imported"], skipped: [], warnings: ["moved a key"], servers });
      }
      if (m && m[2] === "/test") {
        return json({ ok: true, tool_count: 2, server: servers.find((s) => s.name === m[1]) });
      }
      if (m && m[2] === "/key" && method === "PUT") {
        servers = servers.map((s) => (s.name === m[1] ? { ...s, key: "stored" } : s));
        return json(servers.find((s) => s.name === m[1]));
      }
      if (m && !m[2] && method === "PUT") {
        servers = servers.map((s) => (s.name === m[1] ? { ...s, ...body } : s));
        return json(servers.find((s) => s.name === m[1]));
      }
      return json({ detail: "unexpected" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const renderPage = () => render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <McpSettings />
    </QueryClientProvider>,
  );

  it("lists servers with status, target and where the key comes from", async () => {
    renderPage();
    const gh = await screen.findByTestId("mcp-row-github");
    expect(within(gh).getByText("connected · 2 tools")).toBeInTheDocument();
    expect(within(gh).getByText("remote · https://mcp.gh.test/mcp")).toBeInTheDocument();
    expect(within(gh).getByText("key from $GITHUB_TOKEN")).toBeInTheDocument();
    const files = screen.getByTestId("mcp-row-files");
    expect(within(files).getByText("local · npx -y fs-server")).toBeInTheDocument();
    expect(within(files).getByText("no key")).toBeInTheDocument();
    expect(within(screen.getByTestId("mcp-row-paid")).getByText("needs a key")).toBeInTheDocument();
    expect(within(screen.getByTestId("mcp-row-dormant")).getByText("off")).toBeInTheDocument();
  });

  it("adds a server and stores its key separately", async () => {
    renderPage();
    await screen.findByTestId("mcp-row-github");
    fireEvent.click(screen.getByRole("button", { name: "+ Add server" }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Brave" } });
    fireEvent.change(screen.getByLabelText("Transport"), { target: { value: "http" } });
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "https://brave.test/mcp" } });
    fireEvent.change(screen.getByLabelText("Credential"), { target: { value: "key" } });
    const key = screen.getByLabelText("Key");
    expect(key).toHaveAttribute("type", "password");
    fireEvent.change(key, { target: { value: "sk-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    await waitFor(() => expect(calls().some((c) => c.url.endsWith("/brave/key"))).toBe(true));
    const post = calls().find((c) => c.method === "POST")!;
    expect(post.body).toMatchObject({ name: "brave", transport: "http", url: "https://brave.test/mcp", enabled: true });
    expect(JSON.stringify(post.body)).not.toContain("sk-secret");
    expect(calls().find((c) => c.url.endsWith("/brave/key"))!.body).toEqual({ key: "sk-secret" });
  });

  it("imports an mcp.json with the replace option", async () => {
    renderPage();
    await screen.findByTestId("mcp-row-github");
    fireEvent.click(screen.getByRole("button", { name: "Import mcp.json" }));
    fireEvent.change(screen.getByLabelText("mcp.json"), { target: { value: '{"mcpServers":{"x":{"command":"c"}}}' } });
    fireEvent.click(screen.getByLabelText(/Replace existing/));
    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    expect(await screen.findByText(/Imported 1: imported/)).toBeInTheDocument();
    const imp = calls().find((c) => c.url.startsWith("/api/mcp/servers/import"))!;
    expect(imp.url).toContain("replace=true");
    expect(imp.body).toEqual({ mcpServers: { x: { command: "c" } } });
    expect(screen.getByText("moved a key")).toBeInTheDocument();
  });

  it("tests a server and shows the tool count", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("mcp-row-github"));
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText(/Connected · 2 tools/)).toBeInTheDocument();
    expect(calls().some((c) => c.method === "POST" && c.url === "/api/mcp/servers/github/test")).toBe(true);
  });

  it("sets a key and never shows it afterwards", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("mcp-row-paid"));
    fireEvent.click(screen.getByRole("button", { name: "Set key" }));
    const field = screen.getByLabelText("Key for paid");
    expect(field).toHaveAttribute("type", "password");
    fireEvent.change(field, { target: { value: "hunter2-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));
    await waitFor(() => expect(screen.getByText("key saved on this machine", { selector: "dd" })).toBeInTheDocument());
    expect(calls().find((c) => c.url.endsWith("/paid/key"))!.body).toEqual({ key: "hunter2-secret" });
    expect(screen.queryByLabelText("Key for paid")).toBeNull();
    expect(document.body.innerHTML).not.toContain("hunter2-secret");
    expect(screen.getByRole("button", { name: "Clear key" })).toBeInTheDocument();
  });

  it("saves default grants from the tools table", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("mcp-row-github"));
    const box = screen.getByLabelText("create_issue for Reviewer");
    expect(box).not.toBeChecked();
    expect(screen.getByLabelText("create_issue for Planner")).toBeChecked();
    fireEvent.click(box);
    fireEvent.click(screen.getByLabelText("search_code for Builder"));
    fireEvent.click(screen.getByRole("button", { name: "Save defaults" }));
    await waitFor(() => expect(calls().some((c) => c.method === "PUT")).toBe(true));
    const put = calls().find((c) => c.method === "PUT")!;
    expect(put.url).toBe("/api/mcp/servers/github");
    expect(put.body.default_grants).toEqual({
      search_code: ["researcher"],
      create_issue: ["planner", "reviewer"],
    });
    expect(put.body).not.toHaveProperty("tools");
    expect(put.body).not.toHaveProperty("key");
  });

  it("toggles Enabled with a PUT", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("mcp-row-github"));
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => expect(calls().some((c) => c.method === "PUT")).toBe(true));
    expect(calls().find((c) => c.method === "PUT")!.body.enabled).toBe(false);
  });
});

describe("McpToolsField", () => {
  const agent: AgentConfig = { role: "builder" };

  it("shows the role's defaults as chips and stores null for defaults", () => {
    const onChange = vi.fn();
    render(<McpToolsField agent={{ ...agent, mcp_tools: ["github__create_issue"] }} servers={[GITHUB]} onChange={onChange} />);
    expect(screen.getByText("github__create_issue", { selector: "li *" })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/Defaults for this role/));
    expect(onChange).toHaveBeenCalledWith({ mcp_tools: null });
  });

  it("defaults mode lists registry defaults for the role type", () => {
    render(<McpToolsField agent={agent} servers={[GITHUB]} onChange={vi.fn()} />);
    expect(screen.getByText("github__search_code")).toBeInTheDocument();
    expect(screen.queryByText("github__create_issue")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("Choose stores the current effective list, then explicit picks", () => {
    const onChange = vi.fn();
    const { rerender } = render(<McpToolsField agent={agent} servers={[GITHUB]} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText("Choose"));
    expect(onChange).toHaveBeenLastCalledWith({ mcp_tools: ["github__search_code"] });
    rerender(<McpToolsField agent={{ ...agent, mcp_tools: ["github__search_code"] }} servers={[GITHUB]} onChange={onChange} />);
    expect(screen.getByRole("checkbox", { name: "search_code" })).toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: "create_issue" }));
    expect(onChange).toHaveBeenLastCalledWith({ mcp_tools: ["github__search_code", "github__create_issue"] });
    fireEvent.click(screen.getByRole("checkbox", { name: "search_code" }));
    expect(onChange).toHaveBeenLastCalledWith({ mcp_tools: [] });
  });
});
