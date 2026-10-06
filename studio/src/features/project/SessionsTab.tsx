import { Link } from "react-router-dom";
import { ResumeButton } from "../../components/ResumeButton";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { SectionTitle, Table, Td, Th } from "../../components/ui/form-controls";
import { runStatusVariant } from "../../lib/field";
import type { RunMeta } from "../../types";

/** Session numbers: the Nth run, counting oldest first (plan §1 call 10). */
export function sessionNumbers(runs: readonly RunMeta[]): Map<string, number> {
  const sorted = [...runs].sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
  return new Map(sorted.map((r, i) => [r.run_id, i + 1]));
}

/** The project's sessions (runs), newest first, each linking to its run page. */
export function SessionsTab({ runs, onResumed }: { runs: RunMeta[]; onResumed: () => void }) {
  const numbers = sessionNumbers(runs);
  const rows = [...runs].sort((a, b) => (numbers.get(b.run_id) ?? 0) - (numbers.get(a.run_id) ?? 0));
  return (
    <>
      <SectionTitle>Sessions</SectionTitle>
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">No sessions yet. Start one from the Work tab.</p>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Session</Th>
              <Th>Status</Th>
              <Th>Created</Th>
              <Th>Completed agents</Th>
              <Th></Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.run_id}>
                <Td>
                  <Link to={`/runs/${r.run_id}`} className="font-medium">
                    Session {numbers.get(r.run_id)}
                  </Link>
                  <div className="font-mono text-[11px] text-muted-foreground">{r.run_id}</div>
                </Td>
                <Td>
                  <Badge variant={runStatusVariant(r.status)}>{r.status}</Badge>
                </Td>
                <Td className="text-xs text-muted-foreground">{r.created_at?.replace("T", " ").split(".")[0]}</Td>
                <Td className="text-xs">{(r.completed_agents ?? []).join(", ")}</Td>
                <Td>
                  <div className="flex items-center justify-end gap-1.5">
                    <Button asChild variant="outline">
                      <Link to={`/runs/${r.run_id}`}>Open</Link>
                    </Button>
                    {r.status !== "complete" && r.status !== "running" && r.status !== "queued" && (
                      <ResumeButton runId={r.run_id} onResumed={onResumed} />
                    )}
                  </div>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  );
}
