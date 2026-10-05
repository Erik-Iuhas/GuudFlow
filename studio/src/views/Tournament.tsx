import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, trainingApi } from "../api";
import type { TournamentResult } from "../api";
import { Badge } from "../components/ui/badge";
import { Alert, EmptyState, linkClass, Page, Skeleton, Table, Td, Th } from "../components/ui/form-controls";
import { cn } from "../lib/utils";

/**
 * Phase G.4 — leaderboard for one tournament. Polls
 * `/api/training/tournaments/{id}` every 2s until `complete: true`,
 * then stops. Each row is a persona's run with its rubric score
 * (or "Running…" while in flight).
 */
export function Tournament() {
  const { id } = useParams<{ id: string }>();
  const [result, setResult] = useState<TournamentResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<number | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;

    async function tick() {
      try {
        const r = await trainingApi.tournamentResult(id!);
        if (cancelled) return;
        setResult(r);
        if (r.complete && intervalRef.current !== null) {
          window.clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof ApiError ? e.detail : String(e));
        if (intervalRef.current !== null) {
          window.clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
      }
    }

    void tick();
    intervalRef.current = window.setInterval(() => void tick(), 2000);
    return () => {
      cancelled = true;
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current);
      }
    };
  }, [id]);

  if (!id) return null;

  return (
    <Page title="Tournament">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono">{id}</span>
        {result && (
          <Badge variant={result.complete ? "complete" : "running"}>
            {result.complete ? "complete" : "running"}
          </Badge>
        )}
        {result && (
          <span className="text-xs text-muted-foreground">scenario: {result.scenario_id}</span>
        )}
      </div>

      {error && <Alert variant="error" className="mt-3">{error}</Alert>}

      {!result ? (
        !error && <Skeleton className="mt-4 h-24" />
      ) : result.leaderboard.length === 0 ? (
        <EmptyState className="mt-4" title="No participants in this tournament." />
      ) : (
        <div className="mt-4">
          <Table>
            <thead>
              <tr>
                <Th>Rank</Th>
                <Th>Persona</Th>
                <Th>Run</Th>
                <Th className="text-right">Score</Th>
              </tr>
            </thead>
            <tbody>
              {result.leaderboard.map((row, i) => (
                <tr key={row.run_id}>
                  <Td>{row.score === null ? "—" : i + 1}</Td>
                  <Td>
                    <strong className="font-mono">{row.persona_id}</strong>
                  </Td>
                  <Td>
                    <Link to={`/runs/${row.run_id}`} className={cn("font-mono text-xs", linkClass)}>
                      {row.run_id}
                    </Link>
                  </Td>
                  <Td className="text-right">
                    {row.score === null ? (
                      <span className="text-xs text-muted-foreground">Running…</span>
                    ) : (
                      <strong>{row.score.toFixed(2)}</strong>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </Page>
  );
}
