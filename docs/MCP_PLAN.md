# Third-party MCP servers for agent teams

**Status:** requirement captured 2026-10-06. Not built yet.
**Goal:** a project's agents can use standard MCP servers (search, docs, GitHub, Figma, databases)
that a person sets up once in Studio, shares across projects, and turns on per role and per tool.
The setup should be simple and use the standard configuration format.

## Where we are

- opencode supports MCP servers itself (`mcp` in `opencode.json`, local or remote).
- GuudFlow's generated config (`core/harness/config_gen.py`) registers exactly one server: GuudFlow's
  own MCP server (`core/harness/mcp_server.py`). That server carries the board, result and host tools,
  and identifies the role from a per-role bearer token.
- Third-party MCP config (`declared_mcp_servers`, `core/credentials_broker.py`, the `mcp:*` RBAC scopes)
  exists only for the retired legacy harness. Studio has no MCP settings.

## Constraints from the harness rules

- **Credentials never enter sandboxes** (CLAUDE.md rule 17 and plan §5.6). An MCP server's API key or
  OAuth token stays on the host.
- **Sandboxes have no internet egress** (plan §5.8). A remote MCP server cannot be reached from inside
  a sandbox. A local (stdio) server running in the sandbox would have no network and would need its
  secrets inside, so it is ruled out for anything credentialed.
- **Capabilities are decided before the run and only narrow at runtime** (plan §5.7). Widening needs a
  human and takes effect from the next spawn.
- Web content returned by tools is untrusted and is wrapped before it reaches a brief.

## Design

**Proxy on the host.** Third-party servers run (local stdio) or are connected to (remote HTTP) on the
host, and GuudFlow's own MCP server exposes their tools to each role, filtered by that role's grants.

- Agents see one MCP endpoint, as they do today. Tool names are namespaced, for example
  `github__search_issues`.
- A call is checked against the role's grants (the bearer token decides the role, never a tool
  argument), forwarded on the host with the server's credential, logged as a board signal (role,
  server, tool, duration, success; never arguments that look like secrets), and its result is
  returned wrapped as untrusted where it carries outside content.
- Nothing changes in the sandbox network or in `opencode.json` beyond the existing GuudFlow entry.

**Configuration.**

1. **Install-wide servers:** Studio, Settings → MCP servers.
   - Add a server by form (name, local command or remote URL, credential source) or by importing a
     standard `mcp.json` / opencode `mcp` block.
   - Credentials are stored host-only like provider keys (`{data_root}/secrets/`, 0600) or read
     from an environment variable. They are never returned by the API.
   - **Test** connects and lists the server's tools.
   - Each tool has a default grant per role type.
2. **Per project:** the Team tab shows which MCP tools each role gets. It starts from the defaults and
   can be narrowed per role. Widening needs the project's Owner and applies from the next session.
   This is recorded in the `CAPABILITIES` signal at spawn, like other capabilities.
3. **Sharing:** a server is configured once and used by every project and role that has a grant. One
   host-side connection per server is shared across roles and runs, with per-call role attribution.

**Studio:**
- the Settings → MCP servers page (list, status, credential source, tools, default grants per role
  type, which projects use it, recent calls);
- the Team tab's per-role MCP tools column;
- MCP calls in the session's activity and technical views.

## Open questions

- Per-tool rate limits and spend for paid MCP services: count them against the role's budget?
- OAuth-based remote servers: does the host do the OAuth flow once in Studio and refresh tokens?
- Which servers ship as presets (web search and fetch already exist as GuudFlow host tools)?
- Should the escape suite get cases for proxied calls (a role calling a tool it was not granted;
  secrets in tool output)? Yes, before release.
