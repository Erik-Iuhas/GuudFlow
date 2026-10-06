import type { ReactNode } from "react";
import { Badge } from "../../components/ui/badge";
import { Table, Td, Th } from "../../components/ui/form-controls";
import { cn } from "../../lib/utils";
import { formatUsd } from "../../lib/cost";
import { usePlan, useSpec, type CheckState } from "../plan/api";
import type { ProjectSeats } from "../../types";
import type { BacklogItemWire, RunBranchWire } from "./api";
import { groupBacklog } from "./model";
import { acceptanceRef, checkStates, routedToYou } from "./overviewModel";
import { RoutedToYou } from "./RoutedToYou";
import { AskBox } from "./WorkOverview";
import { CHECK_MARK, CHECK_TEXT } from "./SpecResults";

const STATE_LABEL: Record<BacklogItemWire["status"], string> = {
  open: "open", in_run: "in a session", done: "done", dropped: "dropped",
};

/** The role that did the item last, when an earlier session recorded it. */
export function ownerOf(item: BacklogItemWire): string {
  for (const h of [...(item.history ?? [])].reverse()) {
    for (const key of ["owner", "role", "agent"]) {
      const v = h[key];
      if (typeof v === "string" && v) return v;
    }
  }
  return "–";
}

/** Checks met and total over an item's `R#.#:` acceptance lines. */
export function itemChecks(item: BacklogItemWire, states: Record<string, CheckState>): { met: number; total: number } {
  let met = 0;
  let total = 0;
  for (const line of item.acceptance) {
    const ref = acceptanceRef(line);
    if (!ref) continue;
    total += 1;
    if (states[ref.id] === "met") met += 1;
  }
  return { met, total };
}

/**
 * The Work tab at Technical level (storyboard screen 4): the ask box, the full
 * backlog table with spec check states, and what is routed to the user's seats.
 * `children` holds the backlog editor and the review list.
 */
export function WorkTechnical({
  projectId, items, seats, pendingRuns, children,
}: {
  projectId: string;
  items: readonly BacklogItemWire[];
  seats?: ProjectSeats;
  pendingRuns: readonly RunBranchWire[];
  children?: ReactNode;
}) {
  const spec = useSpec(projectId);
  const plan = usePlan(projectId);
  const states = checkStates(spec.data);
  const g = groupBacklog(items);
  const rows = [...g.inRun, ...g.open, ...g.done];
  const routed = routedToYou(seats, pendingRuns, plan.data);
  return (
    <div className="flex flex-col gap-4" data-testid="work-technical">
      <AskBox
        projectId={projectId}
        hint="Message the plan · B3, R8, D1 link automatically"
        placeholder="e.g. B3 should also cover R8"
      />
      <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
        <div className="min-w-0">
          {rows.length === 0 ? (
            <p className="m-0 text-sm text-muted-foreground">The backlog is empty.</p>
          ) : (
            <Table aria-label="Backlog table">
              <thead>
                <tr>
                  <Th>Item</Th><Th>Title</Th><Th>Serves</Th><Th>State</Th><Th>Who</Th><Th>Result</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((i) => {
                  const c = itemChecks(i, states);
                  return (
                    <tr key={i.id} data-testid={`row-${i.id}`}>
                      <Td className="font-mono text-xs">{i.id}</Td>
                      <Td>
                        <div className="font-medium">{i.title}</div>
                        {i.acceptance.length > 0 && (
                          <ul className="m-0 mt-1 list-none p-0 text-xs">
                            {i.acceptance.map((line, n) => {
                              const ref = acceptanceRef(line);
                              const st = ref ? (states[ref.id] ?? "unknown") : null;
                              return (
                                <li key={n} className="flex gap-1.5">
                                  {st && (
                                    <span className={cn("w-3 shrink-0", CHECK_TEXT[st])} aria-label={`${ref!.id} ${st}`}>
                                      {CHECK_MARK[st]}
                                    </span>
                                  )}
                                  <span>{line}</span>
                                </li>
                              );
                            })}
                          </ul>
                        )}
                      </Td>
                      <Td className="font-mono text-xs">{(i.serves ?? []).join(", ") || "–"}</Td>
                      <Td><Badge variant={i.status === "done" ? "complete" : i.status === "in_run" ? "running" : "outline"}>{STATE_LABEL[i.status]}</Badge></Td>
                      <Td className="text-xs">{ownerOf(i)}</Td>
                      <Td className="text-xs">
                        {c.total > 0 ? `${c.met}/${c.total} checks` : "–"}
                        {typeof i.total_cost_usd === "number" && i.total_cost_usd > 0 ? ` · ${formatUsd(i.total_cost_usd)}` : ""}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          )}
        </div>
        <aside>
          <RoutedToYou routed={routed} />
        </aside>
      </div>
      {children}
    </div>
  );
}
