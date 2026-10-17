import { useMemo } from "react";
import { useRunStore } from "../../stores/runStoreContext";

const SHOWN = 4;

interface Row {
  id: string;
  kind: "comment" | "project_update";
  text: string;
  to: string | null;
  at: string;
}

/**
 * Who has been told what (DELIVERED signals): your recent comments and the project changes made
 * during the session, each with the workers that have it in a brief and the ones still waiting.
 * A worker reads a comment at the start of its next turn.
 */
export function Deliveries({ workers }: { workers: readonly string[] }) {
  const signals = useRunStore((s) => s.signals);
  const names = useRunStore((s) => s.names);
  const { rows, told } = useMemo(() => {
    const rows: Row[] = [];
    const told: Record<string, string[]> = {};
    for (const sig of signals) {
      if (sig.type === "USER_COMMENT" || sig.type === "USER_COMMENT_PRIORITY") {
        let to: string | null = null;
        try {
          const d = sig.detail ? (JSON.parse(sig.detail) as { to?: unknown }) : null;
          to = d && typeof d.to === "string" ? d.to : null;
        } catch {
          /* plain comment */
        }
        rows.push({ id: sig.id, kind: "comment", text: sig.summary, to, at: sig.timestamp });
      } else if (sig.type === "PROJECT_UPDATED") {
        const d = sig.data as { update?: string; changes?: string[] } | null;
        if (!d?.update) continue;
        rows.push({ id: d.update, kind: "project_update", text: (d.changes ?? []).join("; "), to: null, at: sig.timestamp });
      } else if (sig.type === "DELIVERED") {
        const d = sig.data as { ref?: string; role?: string } | null;
        if (d?.ref && d.role) (told[d.ref] ??= []).push(d.role);
      }
    }
    return { rows: rows.slice(-SHOWN).reverse(), told };
  }, [signals]);
  if (rows.length === 0) return null;
  const label = (r: string) => names[r] ?? r;
  return (
    <ul aria-label="Who has been told" className="m-0 mt-2 flex list-none flex-col gap-1 p-0 text-xs" data-testid="deliveries">
      {rows.map((row) => {
        const audience = row.to ? [row.to] : workers;
        const got = told[row.id] ?? [];
        const waiting = audience.filter((r) => !got.includes(r));
        return (
          <li key={row.id} className="flex flex-wrap items-center gap-1.5" data-testid={`delivery-${row.id}`}>
            <span className="font-medium">
              {row.kind === "project_update" ? "Project change" : row.to ? `To ${label(row.to)}` : "To everyone"}:
            </span>
            <span className="max-w-[28rem] truncate text-muted-foreground" title={row.text}>{row.text}</span>
            {got.map((r) => (
              <span key={r} className="rounded border border-status-complete/40 px-1 text-status-complete" title="in this worker's brief">
                ✓ {label(r)}
              </span>
            ))}
            {waiting.length > 0 && (
              <span className="text-muted-foreground" title="told at the start of their next turn">
                waiting: {waiting.map(label).join(", ")}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
