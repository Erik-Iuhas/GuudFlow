import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BoardFeed } from "../../BoardFeed";
import { AgentStateCard } from "../../AgentStateCard";
import type { Signal } from "../../../types";
import type { AgentLiveSummary } from "../../../lib/iterations";

let seq = 0;
function sig(type: string, detail: unknown, over: Partial<Signal> = {}): Signal {
  seq += 1;
  return {
    id: `s${seq}`,
    type,
    summary: `summary of ${type}`,
    detail: detail === null ? null : typeof detail === "string" ? detail : JSON.stringify(detail),
    from_agent: "builder",
    from_user: false,
    priority: "normal",
    iteration: 1,
    seen_by: [],
    timestamp: "2026-01-01T00:00:00.000000",
    ...over,
  };
}

describe("TOOL_CALL row", () => {
  it("shows ok badge, target, duration, and expands input/output as text", () => {
    const detail = {
      tool: "edit", status: "completed", duration_ms: 1500,
      input: { filePath: "src/a.py", newString: "<img src=x onerror=alert(1)>" },
      output: "Edit applied",
    };
    render(<BoardFeed signals={[sig("TOOL_CALL", detail, { summary: "builder: edit src/a.py → ok" })]} />);
    expect(screen.getByText("edit")).toBeInTheDocument();
    expect(screen.getByText("src/a.py")).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument();
    expect(screen.getByText("1.5 s")).toBeInTheDocument();
    expect(screen.queryByText("Edit applied")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /edit/ }));
    expect(screen.getByText("Edit applied")).toBeInTheDocument();
    // Rendered as text, never as markup.
    expect(screen.getByText(/onerror=alert/)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("shows error badge and the error text when expanded", () => {
    const detail = {
      tool: "bash", status: "error", duration_ms: 40,
      input: { command: "pytest -x" }, error: "exit code 1: 3 failed",
    };
    render(<BoardFeed signals={[sig("TOOL_CALL", detail)]} />);
    expect(screen.getByText("error")).toBeInTheDocument();
    expect(screen.getByText("pytest -x")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /bash/ }));
    expect(screen.getByText("exit code 1: 3 failed")).toBeInTheDocument();
  });
});

describe("AGENT_RESULT", () => {
  it("renders an agent result with status, summary, files, cost and tokens", () => {
    const detail = {
      result: { status: "done", summary: "Implemented the login endpoint.", files_changed: ["a.py"] },
      cost: 0.0123, tokens: 4200, tool_calls: 7, commits: 2, files: ["api/login.py", "tests/test_login.py"],
    };
    render(<BoardFeed signals={[sig("AGENT_RESULT", detail)]} />);
    expect(screen.getByText("done")).toBeInTheDocument();
    expect(screen.getByText("Implemented the login endpoint.")).toBeInTheDocument();
    expect(screen.getByText(/api\/login\.py, tests\/test_login\.py/)).toBeInTheDocument();
    expect(screen.getByText(/\$0\.0123/)).toBeInTheDocument();
    expect(screen.getByText(/4,200 tokens/)).toBeInTheDocument();
  });

  it("renders a reviewer verdict with its issue list", () => {
    const detail = {
      result: {
        verdict: "fail",
        issues: [{ file: "api/login.py", detail: "Password is compared without constant-time check." }],
      },
      cost: 0.5, tokens: 1000, tool_calls: 3, commits: 0, files: [],
    };
    render(<BoardFeed signals={[sig("AGENT_RESULT", detail, { from_agent: "reviewer" })]} />);
    expect(screen.getByText("fail")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Review issues" });
    expect(list).toHaveTextContent("api/login.py");
    expect(list).toHaveTextContent("constant-time");
  });
});

describe("AGENT_TODO", () => {
  it("renders a checklist with statuses", () => {
    const detail = [
      { content: "Write tests", status: "completed", priority: "high" },
      { content: "Implement endpoint", status: "in_progress", priority: "medium" },
      { content: "Update docs", status: "pending", priority: "low" },
    ];
    render(<BoardFeed signals={[sig("AGENT_TODO", detail)]} />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveAttribute("data-status", "completed");
    expect(items[1]).toHaveAttribute("data-status", "in_progress");
    expect(screen.getByText("Update docs")).toBeInTheDocument();
  });
});

describe("CAPABILITIES", () => {
  const detail = {
    role_type: "backend",
    requested: {}, effective: { sandbox: "host_dev", web: "off", shell: "project", packages: "off", subagents: "off", browser: "off", vision: "off" },
    digest: "abc123", mcp_tools: [], model: "anthropic/claude-sonnet",
    warnings: ["HOST MODE: this agent runs on the host without a sandbox."],
  };

  it("renders chips and prominently shows warnings", () => {
    render(<BoardFeed signals={[sig("CAPABILITIES", detail)]} />);
    expect(screen.getByText("sandbox: host_dev")).toBeInTheDocument();
    expect(screen.getByText("shell: project")).toBeInTheDocument();
    expect(screen.getByText("vision: off")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("HOST MODE");
  });

  it("AgentStateCard shows a warning icon with the text as title", () => {
    const summary: AgentLiveSummary = {
      role: "builder", state: "running", iteration: 1, lastCommit: null,
      lastCommitMessage: null, claimedFiles: [], unreadComments: 0,
    };
    render(<AgentStateCard summary={summary} allSignals={[sig("CAPABILITIES", detail)]} />);
    const icon = screen.getByRole("img", { name: "Capability warning" });
    expect(icon).toHaveAttribute("title", expect.stringContaining("HOST MODE"));
  });

  it("AgentStateCard shows no warning icon when there are none", () => {
    const summary: AgentLiveSummary = {
      role: "builder", state: "running", iteration: 1, lastCommit: null,
      lastCommitMessage: null, claimedFiles: [], unreadComments: 0,
    };
    render(<AgentStateCard summary={summary} allSignals={[sig("CAPABILITIES", { ...detail, warnings: [] })]} />);
    expect(screen.queryByRole("img", { name: "Capability warning" })).toBeNull();
  });
});

describe("THINKING", () => {
  it("collapses long text and shows short text directly", () => {
    const long = "reasoning ".repeat(60);
    render(<BoardFeed signals={[
      sig("THINKING", long),
      sig("THINKING", "short thought", { summary: "builder: short" }),
    ]} />);
    expect(document.querySelectorAll("details.harness-collapsible")).toHaveLength(1);
    expect(screen.getByText("short thought")).toBeInTheDocument();
  });
});

describe("malformed JSON detail", () => {
  it.each(["TOOL_CALL", "AGENT_RESULT", "AGENT_TODO", "CAPABILITIES"])(
    "%s falls back to summary and raw detail",
    (type) => {
      render(<BoardFeed signals={[sig(type, "{not json", { summary: `fallback summary ${type}` })]} />);
      expect(screen.getByText(`fallback summary ${type}`)).toBeInTheDocument();
      expect(screen.getByText("{not json")).toBeInTheDocument();
    },
  );
});


describe("parseToolPermissionContext backend format", () => {
  it("reads the trailing context JSON even when patterns contain ; and ,", async () => {
    const { parseToolPermissionContext } = await import("../parse");
    const detail =
      'approval_id=appr_1; kind=tool_permission; context={"role": "builder", "permission": "bash", "patterns": ["a; b, c"]}';
    expect(parseToolPermissionContext(detail)).toEqual({
      role: "builder",
      permission: "bash",
      patterns: ["a; b, c"],
    });
  });
});
