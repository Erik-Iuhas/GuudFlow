import { useEffect, useState, type FormEvent } from "react";
import { getToken } from "../../api";
import { Button } from "../../components/ui/button";
import { Alert, Input, Label } from "../../components/ui/form-controls";

// Settings → Certificate (api/routes/tls.py): the company CA to trust, a backup for
// GUUDFLOW_CA_BUNDLE in .env. The .env path wins when its file exists.

export interface TlsStatus {
  env: string;
  app: string;
  effective: string | null;
  source: "env" | "app" | null;
  certs: string[];
  error: string | null;
}

async function call(method: "GET" | "PUT", body?: unknown): Promise<TlsStatus> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch("/api/tls", { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { detail?: string }).detail ?? `HTTP ${res.status}`);
  return data as TlsStatus;
}

export function CertSettings() {
  const [status, setStatus] = useState<TlsStatus | null>(null);
  const [path, setPath] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    call("GET")
      .then((s) => { setStatus(s); setPath(s.app); })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  async function save(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      setStatus(await call("PUT", { ca_bundle: path.trim() }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="flex max-w-3xl flex-col gap-3" aria-label="Certificate" data-testid="cert-settings">
      <div>
        <h2 className="m-0 text-base font-semibold">Certificate</h2>
        <p className="m-0 mt-1 text-sm text-muted-foreground">
          Your company's root CA, for a proxy that re-signs HTTPS. <code>GUUDFLOW_CA_BUNDLE</code> in .env is
          used first; the path here is used when .env has none or its file is not found. PEM or .cer; a
          path like <code>C:\certs\corp.pem</code> works from WSL too. Applies to the next connection.
        </p>
      </div>
      <form onSubmit={save} className="flex flex-wrap items-end gap-2">
        <div className="flex min-w-[320px] flex-1 flex-col gap-1">
          <Label htmlFor="ca-bundle">CA file path</Label>
          <Input id="ca-bundle" value={path} placeholder="/home/you/certs/corp-ca.pem"
                 onChange={(e) => setPath(e.target.value)} />
        </div>
        <Button type="submit" size="sm" disabled={saving}>{saving ? "Saving…" : "Save"}</Button>
      </form>
      {error && <Alert variant="error">{error}</Alert>}
      {status && (
        <div className="text-sm" data-testid="cert-status">
          {status.error && <Alert variant="error">{status.error}</Alert>}
          {!status.error && status.effective && (
            <p className="m-0">
              In use: <code>{status.effective}</code> (from {status.source === "env" ? ".env" : "this page"})
            </p>
          )}
          {!status.error && !status.effective && <p className="m-0 text-muted-foreground">No custom CA: public roots only.</p>}
          {status.env && status.source !== "env" && (
            <p className="m-0 text-muted-foreground">.env has <code>{status.env}</code>, which is not used.</p>
          )}
          {status.certs.length > 0 && (
            <ul className="m-0 mt-1 pl-5 text-muted-foreground">
              {status.certs.map((c) => <li key={c}>{c}</li>)}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
