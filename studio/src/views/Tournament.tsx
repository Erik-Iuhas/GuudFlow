import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, trainingApi } from "../api";
import type { TournamentResult } from "../api";

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
    <div className="page">
      <h1>Tournament</h1>
      <div className="row" style={{ gap: 12, alignItems: "center" }}>
        <span className="mono">{id}</span>
        {result && (
          <span
            className={`tag ${
              result.complete ? "tag-status-complete" : "tag-status-running"
            }`}
          >
            {result.complete ? "complete" : "running"}
          </span>
        )}
        {result && (
          <span className="muted small">scenario: {result.scenario_id}</span>
        )}
      </div>

      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}

      {!result ? (
        <p className="muted">loading…</p>
      ) : result.leaderboard.length === 0 ? (
        <p className="muted">No participants in this tournament.</p>
      ) : (
        <table className="list" style={{ marginTop: 16 }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>Rank</th>
              <th style={{ textAlign: "left" }}>Persona</th>
              <th style={{ textAlign: "left" }}>Run</th>
              <th style={{ textAlign: "right" }}>Score</th>
            </tr>
          </thead>
          <tbody>
            {result.leaderboard.map((row, i) => (
              <tr key={row.run_id}>
                <td>{row.score === null ? "—" : i + 1}</td>
                <td>
                  <strong className="mono">{row.persona_id}</strong>
                </td>
                <td>
                  <Link to={`/runs/${row.run_id}`} className="mono small">
                    {row.run_id}
                  </Link>
                </td>
                <td style={{ textAlign: "right" }}>
                  {row.score === null ? (
                    <span className="muted small">Running…</span>
                  ) : (
                    <strong>{row.score.toFixed(2)}</strong>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
