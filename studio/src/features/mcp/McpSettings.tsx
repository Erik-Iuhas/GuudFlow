import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { Badge, type BadgeProps } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "../../components/ui/dialog";
import { Alert, Input, Label, Select, Skeleton, Table, Td, Th, Textarea } from "../../components/ui/form-controls";
import { Switch } from "../../components/ui/switch";
import {
  mcpApi, mcpKey, useMcpMutation, useMcpServers,
  type McpServerBody, type McpServerWire, type McpTestResult,
} from "./api";
import {
  GRANT_ROLES, joinArgs, keyLabel, serverStatus, splitArgs, targetLabel, toBody, toggleGrant,
  type ChipStatus,
} from "./model";

const STATUS_VARIANT: Record<ChipStatus, BadgeProps["variant"]> = {
  connected: "complete",
  "needs-key": "waiting",
  off: "outline",
  error: "failed",
  untested: "secondary",
};

/**
 * Settings → MCP servers: the third-party tool servers every project can use. Keys are stored
 * on this machine, never shown again, and never reach an agent.
 */
export function McpSettings() {
  const servers = useMcpServers();
  const [selected, setSelected] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const list = servers.data ?? [];
  const current = list.find((s) => s.name === selected) ?? null;
  useEffect(() => {
    if (selected && servers.data && !servers.data.some((s) => s.name === selected)) setSelected(null);
  }, [selected, servers.data]);

  return (
    <section className="flex flex-col gap-4" aria-label="MCP servers" data-testid="mcp-settings">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="m-0 text-base font-semibold">MCP servers</h2>
          <p className="m-0 mt-1 max-w-xl text-sm text-muted-foreground">
            Set up once, shared by every project. Servers run on this machine; agents reach them through
            GuudFlow and never see their keys.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setImporting(true)}>Import mcp.json</Button>
          <Button size="sm" onClick={() => setAdding(true)}>+ Add server</Button>
        </div>
      </div>

      {servers.isLoading && <Skeleton className="h-24" />}
      {servers.error && <Alert variant="error">Could not load MCP servers: {servers.error.message}</Alert>}
      {servers.data && list.length === 0 && (
        <p className="m-0 rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
          No MCP servers yet. Add one, or import an existing mcp.json.
        </p>
      )}

      <div className="grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0" aria-label="Servers">
          {list.map((s) => {
            const status = serverStatus(s, errors[s.name]);
            return (
              <li key={s.name}>
                <button
                  type="button"
                  data-testid={`mcp-row-${s.name}`}
                  aria-pressed={selected === s.name}
                  onClick={() => setSelected(s.name)}
                  className={`w-full rounded-lg border px-3 py-2 text-left text-card-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                    selected === s.name ? "border-primary bg-accent" : "border-border bg-card hover:bg-accent"
                  }`}
                >
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{s.name}</span>
                    <Badge variant={STATUS_VARIANT[status.kind]}>{status.label}</Badge>
                  </span>
                  <span className="mt-0.5 block truncate font-mono text-xs text-muted-foreground">{targetLabel(s)}</span>
                  <span className="block text-xs text-muted-foreground">{keyLabel(s)}</span>
                </button>
              </li>
            );
          })}
        </ul>

        {current ? (
          <ServerDetail
            key={current.name}
            server={current}
            error={errors[current.name]}
            onTested={(r) => setErrors((e) => {
              const { [current.name]: _old, ...rest } = e;
              return r.ok ? rest : { ...rest, [current.name]: r.error ?? "test failed" };
            })}
          />
        ) : (
          list.length > 0 && (
            <p className="m-0 p-4 text-sm text-muted-foreground">Select a server to see its tools and settings.</p>
          )
        )}
      </div>

      {adding && (
        <ServerFormDialog
          onClose={() => setAdding(false)}
          onSaved={(name) => { setAdding(false); setSelected(name); }}
        />
      )}
      {importing && <ImportDialog onClose={() => setImporting(false)} />}
    </section>
  );
}

function ServerDetail({
  server, error, onTested,
}: {
  server: McpServerWire;
  error?: string;
  onTested: (r: McpTestResult) => void;
}) {
  const [test, setTest] = useState<McpTestResult | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  const [key, setKey] = useState("");
  const [grants, setGrants] = useState(server.default_grants);
  const [savedGrants, setSavedGrants] = useState(false);

  const replace = useMcpMutation((body: McpServerBody) => mcpApi.replace(server.name, body));
  const remove = useMcpMutation(() => mcpApi.remove(server.name));
  const setKeyM = useMcpMutation((k: string) => mcpApi.setKey(server.name, k));
  const clearKeyM = useMcpMutation(() => mcpApi.clearKey(server.name));
  const problem = replace.error ?? remove.error ?? setKeyM.error ?? clearKeyM.error;

  const grantsDirty = JSON.stringify(grants) !== JSON.stringify(server.default_grants);
  const status = serverStatus(server, error);

  async function runTest() {
    setTesting(true);
    setTest(null);
    setTestError(null);
    try {
      const r = await mcpApi.test(server.name);
      setTest(r);
      onTested(r);
    } catch (e) {
      setTestError(e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-card p-4 text-card-foreground" data-testid="mcp-detail">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="m-0 text-base font-semibold">{server.name}</h3>
        <Badge variant={STATUS_VARIANT[status.kind]}>{status.label}</Badge>
        <span className="ml-auto flex items-center gap-2 text-sm">
          <Label htmlFor={`mcp-enabled-${server.name}`} className="m-0">Enabled</Label>
          <Switch
            id={`mcp-enabled-${server.name}`}
            checked={server.enabled}
            disabled={replace.isPending}
            onCheckedChange={(on) => replace.mutate({ ...toBody(server), enabled: on })}
          />
        </span>
      </div>

      <dl className="m-0 mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Transport</dt>
        <dd className="m-0 font-mono text-xs">{targetLabel(server)}</dd>
        <dt className="text-muted-foreground">Credential</dt>
        <dd className="m-0">{keyLabel(server)}</dd>
      </dl>

      <div className="mt-3 flex flex-wrap items-center gap-1">
        <Button size="sm" variant="outline" onClick={() => void runTest()} disabled={testing}>
          {testing ? "Testing…" : "Test"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>Edit</Button>
        <Button size="sm" variant="ghost" aria-expanded={keyOpen} onClick={() => setKeyOpen((v) => !v)}>
          {server.key === "stored" ? "Replace key" : "Set key"}
        </Button>
        {server.key === "stored" && (
          <Button size="sm" variant="ghost" disabled={clearKeyM.isPending} onClick={() => clearKeyM.mutate(undefined)}>
            Clear key
          </Button>
        )}
        <Button size="sm" variant="ghost" disabled={remove.isPending}
          onClick={() => {
            if (window.confirm(`Remove the MCP server "${server.name}" and its saved key?`)) remove.mutate(undefined);
          }}>
          Remove
        </Button>
      </div>

      {keyOpen && (
        <form
          className="mt-2 flex flex-wrap items-center gap-2"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            if (!key.trim()) return;
            setKeyM.mutate(key.trim(), { onSuccess: () => { setKey(""); setKeyOpen(false); } });
          }}
        >
          <Input type="password" autoComplete="off" className="max-w-xs" value={key} autoFocus
            aria-label={`Key for ${server.name}`} placeholder="Paste a key"
            onChange={(e) => setKey(e.target.value)} />
          <Button type="submit" size="sm" disabled={!key.trim() || setKeyM.isPending}>Save key</Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => { setKey(""); setKeyOpen(false); }}>Cancel</Button>
        </form>
      )}

      {problem && <Alert variant="error" className="mt-2">{problem.message}</Alert>}
      {testError && <Alert variant="error" className="mt-2">{testError}</Alert>}
      {test && (
        <p className={`m-0 mt-2 text-sm ${test.ok ? "text-status-complete" : "text-status-failed"}`} role="status">
          {test.ok ? `✓ Connected · ${test.tool_count ?? 0} tool${test.tool_count === 1 ? "" : "s"}` : `✕ ${test.error ?? "test failed"}`}
        </p>
      )}

      <h4 className="m-0 mb-1 mt-4 text-sm font-semibold">Tools</h4>
      {server.tools.length === 0 ? (
        <p className="m-0 text-sm text-muted-foreground">No tools yet. Press Test to connect and list them.</p>
      ) : (
        <div className="overflow-x-auto">
          <Table aria-label={`${server.name} tools`}>
            <thead>
              <tr>
                <Th>Tool</Th>
                <Th>Description</Th>
                {GRANT_ROLES.map(([, label]) => <Th key={label}>{label}</Th>)}
              </tr>
            </thead>
            <tbody>
              {server.tools.map((t) => (
                <tr key={t.name}>
                  <Td className="font-mono text-xs">{t.name}</Td>
                  <Td className="text-xs text-muted-foreground">{t.description}</Td>
                  {GRANT_ROLES.map(([role, label]) => (
                    <Td key={role}>
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-primary"
                        aria-label={`${t.name} for ${label}`}
                        checked={(grants[t.name] ?? []).includes(role)}
                        onChange={(e) => { setSavedGrants(false); setGrants((g) => toggleGrant(g, t.name, role, e.target.checked)); }}
                      />
                    </Td>
                  ))}
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      {server.tools.length > 0 && (
        <div className="mt-2 flex items-center gap-2">
          <Button size="sm" disabled={!grantsDirty || replace.isPending}
            onClick={() => replace.mutate({ ...toBody(server), default_grants: grants }, { onSuccess: () => setSavedGrants(true) })}>
            Save defaults
          </Button>
          {grantsDirty && <Button size="sm" variant="ghost" onClick={() => setGrants(server.default_grants)}>Discard</Button>}
          {savedGrants && !grantsDirty && <span className="text-sm text-status-complete" role="status">Saved.</span>}
        </div>
      )}
      <p className="m-0 mt-2 text-xs text-muted-foreground">
        These are defaults for new teams. Each project&apos;s Team tab can narrow them per role; widening one needs
        the project&apos;s Owner and applies from the next session.
      </p>

      {editing && (
        <ServerFormDialog existing={server} onClose={() => setEditing(false)} onSaved={() => setEditing(false)} />
      )}
    </div>
  );
}

type CredentialMode = "none" | "env" | "key";

function ServerFormDialog({
  existing, onClose, onSaved,
}: {
  existing?: McpServerWire;
  onClose: () => void;
  onSaved: (name: string) => void;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [transport, setTransport] = useState<"stdio" | "http">(existing?.transport ?? "stdio");
  const [command, setCommand] = useState(existing?.command ?? "");
  const [args, setArgs] = useState(joinArgs(existing?.args ?? []));
  const [url, setUrl] = useState(existing?.url ?? "");
  const [mode, setMode] = useState<CredentialMode>(existing?.credential.env ? "env" : "none");
  const [envVar, setEnvVar] = useState(existing?.credential.env ?? "");
  const [key, setKey] = useState("");
  const [enabled, setEnabled] = useState(existing?.enabled ?? true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const cleanName = name.trim().toLowerCase();
  const valid =
    (existing !== undefined || /^[a-z][a-z0-9-]{0,31}$/.test(cleanName)) &&
    (transport === "stdio" ? command.trim() !== "" : /^https?:\/\/\S+$/.test(url.trim())) &&
    (mode !== "env" || /^[A-Za-z_][A-Za-z0-9_]*$/.test(envVar.trim())) &&
    (mode !== "key" || existing !== undefined || key.trim() !== "");

  const queryClient = useQueryClient();

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    const body: McpServerBody = {
      name: existing?.name ?? cleanName,
      transport,
      command: transport === "stdio" ? command.trim() : "",
      args: transport === "stdio" ? splitArgs(args) : [],
      env: existing?.env ?? [],
      url: transport === "http" ? url.trim() : "",
      headers: existing?.headers ?? {},
      credential: mode === "env" ? { env: envVar.trim() } : {},
      enabled,
      default_grants: existing?.default_grants ?? {},
    };
    try {
      if (existing) await mcpApi.replace(existing.name, body);
      else await mcpApi.add(body);
      if (mode === "key" && key.trim()) await mcpApi.setKey(body.name, key.trim());
      setKey("");
      await queryClient.invalidateQueries({ queryKey: mcpKey });
      onSaved(body.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setKey("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{existing ? `Edit ${existing.name}` : "Add an MCP server"}</DialogTitle>
          <DialogDescription>
            Local servers run as a command on this machine; remote servers are reached over HTTP.
          </DialogDescription>
        </DialogHeader>
        <form className="grid gap-3" onSubmit={(e) => void submit(e)} aria-label="MCP server">
          <div>
            <Label htmlFor="mcp-name">Name</Label>
            <Input id="mcp-name" placeholder="github" value={name} disabled={existing !== undefined}
              onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="mcp-transport">Transport</Label>
            <Select id="mcp-transport" value={transport} onChange={(e) => setTransport(e.target.value as "stdio" | "http")}>
              <option value="stdio">Local (runs a command)</option>
              <option value="http">Remote (URL)</option>
            </Select>
          </div>
          {transport === "stdio" ? (
            <>
              <div>
                <Label htmlFor="mcp-command">Command</Label>
                <Input id="mcp-command" className="font-mono" placeholder="npx" value={command}
                  onChange={(e) => setCommand(e.target.value)} />
              </div>
              <div>
                <Label htmlFor="mcp-args">Arguments</Label>
                <Input id="mcp-args" className="font-mono" placeholder="-y @modelcontextprotocol/server-github" value={args}
                  onChange={(e) => setArgs(e.target.value)} />
              </div>
            </>
          ) : (
            <div>
              <Label htmlFor="mcp-url">URL</Label>
              <Input id="mcp-url" className="font-mono" placeholder="https://mcp.example.com/mcp" value={url}
                onChange={(e) => setUrl(e.target.value)} />
            </div>
          )}
          <div>
            <Label htmlFor="mcp-cred">Credential</Label>
            <Select id="mcp-cred" value={mode} onChange={(e) => setMode(e.target.value as CredentialMode)}>
              <option value="none">No key</option>
              <option value="env">From an environment variable</option>
              <option value="key">Set a key</option>
            </Select>
          </div>
          {mode === "env" && (
            <div>
              <Label htmlFor="mcp-env">Environment variable</Label>
              <Input id="mcp-env" className="font-mono" placeholder="GITHUB_TOKEN" value={envVar}
                onChange={(e) => setEnvVar(e.target.value)} />
            </div>
          )}
          {mode === "key" && (
            <div>
              <Label htmlFor="mcp-key">Key</Label>
              <Input id="mcp-key" type="password" autoComplete="off" value={key}
                placeholder={existing?.key === "stored" ? "Leave empty to keep the saved key" : "Paste a key"}
                onChange={(e) => setKey(e.target.value)} />
              <p className="m-0 mt-1 text-xs text-muted-foreground">Saved on this machine, never shown again.</p>
            </div>
          )}
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={enabled} onCheckedChange={setEnabled} aria-label="Enabled" />
            Enabled
          </label>
          {error && <Alert variant="error">{error}</Alert>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={!valid || busy}>{existing ? "Save" : "Add server"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ImportDialog({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState("");
  const [replace, setReplace] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ imported: string[]; skipped: { name: string; reason: string }[]; warnings: string[] } | null>(null);
  const run = useMcpMutation((doc: unknown) => mcpApi.importJson(doc, replace));

  function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    let doc: unknown;
    try {
      doc = JSON.parse(text);
    } catch {
      setError("That is not valid JSON.");
      return;
    }
    run.mutate(doc, {
      onSuccess: (r) => { setResult(r); setText(""); },
      onError: (err) => setError(err.message),
    });
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Import mcp.json</DialogTitle>
          <DialogDescription>
            Paste a standard mcp.json (<code>mcpServers</code>) or an opencode <code>mcp</code> block. Keys found in it move to
            this machine&apos;s secret store.
          </DialogDescription>
        </DialogHeader>
        <form className="grid gap-3" onSubmit={submit} aria-label="Import mcp.json">
          <Textarea rows={8} className="font-mono text-xs" aria-label="mcp.json" value={text}
            placeholder={'{ "mcpServers": { "github": { "command": "npx", "args": ["-y", "…"] } } }'}
            onChange={(e) => setText(e.target.value)} />
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" className="h-4 w-4 accent-primary" checked={replace}
              onChange={(e) => setReplace(e.target.checked)} />
            Replace existing servers with the same name
          </label>
          {error && <Alert variant="error">{error}</Alert>}
          {result && (
            <div className="text-sm" role="status">
              <p className="m-0">Imported {result.imported.length}: {result.imported.join(", ") || "nothing"}.</p>
              {result.skipped.map((s) => (
                <p key={s.name} className="m-0 text-muted-foreground">Skipped {s.name}: {s.reason}</p>
              ))}
              {result.warnings.map((w) => <p key={w} className="m-0 text-status-blocked">{w}</p>)}
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>{result ? "Done" : "Cancel"}</Button>
            <Button type="submit" disabled={!text.trim() || run.isPending}>Import</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
