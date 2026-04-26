import { useEffect, useState } from "react";
import { ApiError, personasApi } from "../api";
import type { PersonaRankingRow } from "../api";

/**
 * Phase G.0 — leaderboard of personas ranked by training rubric
 * score. Wraps F.1.3's `/api/personas/rankings`. Optional filters:
 * role_type (substring against id + specialty), scenario id.
 */
export function RankingsPanel() {
  const [rows, setRows] = useState<PersonaRankingRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [roleType, setRoleType] = useState("");
  const [scenario, setScenario] = useState("");

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const opts: Parameters<typeof personasApi.rankings>[0] = {};
      if (roleType.trim()) opts.role_type = roleType.trim();
      if (scenario.trim()) opts.scenario = scenario.trim();
      setRows(await personasApi.rankings(opts));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <p className="muted small">
        Ranked by average training rubric score. Personas without
        any training-mode runs don't appear here.
      </p>

      <div className="row" style={{ gap: 8, marginBottom: 12 }}>
        <input
          type="text"
          value={roleType}
          onChange={(e) => setRoleType(e.target.value)}
          placeholder="role_type (e.g. frontend)"
          style={{ flex: 1 }}
        />
        <input
          type="text"
          value={scenario}
          onChange={(e) => setScenario(e.target.value)}
          placeholder="scenario id (e.g. hello_python_v1)"
          style={{ flex: 1 }}
        />
        <button onClick={() => void load()} disabled={loading}>
          {loading ? "…" : "Filter"}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {rows.length === 0 ? (
        <p className="muted small">
          No ranked personas yet. Train some via the Training tab to
          populate this leaderboard.
        </p>
      ) : (
        <table className="list">
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>#</th>
              <th style={{ textAlign: "left" }}>Persona</th>
              <th style={{ textAlign: "left" }}>Specialty</th>
              <th style={{ textAlign: "right" }}>Avg score</th>
              <th style={{ textAlign: "right" }}>Runs</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.persona_id}>
                <td>{i + 1}</td>
                <td>
                  <strong>{row.name}</strong>
                  <div className="small muted mono">{row.persona_id}</div>
                </td>
                <td className="small">{row.specialty || "—"}</td>
                <td style={{ textAlign: "right" }}>
                  {row.average_score.toFixed(2)}
                </td>
                <td style={{ textAlign: "right" }}>{row.n_runs}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
