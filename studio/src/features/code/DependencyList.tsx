import { useQuery } from "@tanstack/react-query";
import { fetchProjectDeps } from "./api";

const MANIFEST = { npm: "package.json", pip: "requirements.txt" } as const;

/** The project's shared dependencies: what each worker added, who approved it, in which session. */
export function DependencyList({ projectId, names }: { projectId: string; names?: Record<string, string> }) {
  const q = useQuery({
    queryKey: ["code", projectId, "deps"] as const,
    queryFn: () => fetchProjectDeps(projectId),
  });
  if (q.isPending) return <p className="m-0 p-3 text-sm text-muted-foreground" role="status">Loading dependencies…</p>;
  if (q.isError) return <p className="m-0 p-3 text-sm text-destructive" role="alert">Could not load dependencies: {q.error.message}</p>;
  const envs = q.data.environments;
  if (envs.length === 0) {
    return (
      <p className="m-0 p-3 text-sm text-muted-foreground">
        No shared dependencies yet. A worker adds them by pinning them in package.json or
        requirements.txt and calling <span className="font-mono">sync_dependencies</span>.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4 overflow-auto p-3" data-testid="dependency-list">
      {envs.map((env) => (
        <section key={env.manager} aria-label={`${env.manager} dependencies`}>
          <h3 className="m-0 mb-1 text-sm font-semibold">
            {env.manager} <span className="font-normal text-muted-foreground">· from {MANIFEST[env.manager]} · {env.packages.length} packages</span>
          </h3>
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="py-1 pr-3 font-medium">Package</th>
                <th className="py-1 pr-3 font-medium">Version</th>
                <th className="py-1 pr-3 font-medium">Added by</th>
                <th className="py-1 pr-3 font-medium">Approved</th>
                <th className="py-1 font-medium">Session</th>
              </tr>
            </thead>
            <tbody>
              {env.packages.map((p) => (
                <tr key={p.name} className="border-t border-border">
                  <td className="py-1 pr-3 font-mono">{p.name}</td>
                  <td className="py-1 pr-3 font-mono">{p.version}</td>
                  <td className="py-1 pr-3">{names?.[p.added_by] ? `${names[p.added_by]} · ${p.added_by}` : p.added_by || "—"}</td>
                  <td className="py-1 pr-3">{p.approved_by === "human" ? "by a person" : p.approved_by || "—"}</td>
                  <td className="py-1 font-mono">{p.run_id || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}
