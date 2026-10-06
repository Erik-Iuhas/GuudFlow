import { useEffect, useState } from "react";
import { ApiError, personasApi } from "../api";
import type { PersonaRankingRow } from "../api";
import { Button } from "./ui/button";
import { Alert, EmptyState, Input, Table, Td, Th } from "./ui/form-controls";

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
      <p className="mb-3 mt-0 text-sm text-muted-foreground">
        Ranked by average training rubric score. Personas without
        any training-mode runs don't appear here.
      </p>

      <div className="mb-3 flex flex-wrap gap-2">
        <Input
          type="text"
          value={roleType}
          onChange={(e) => setRoleType(e.target.value)}
          placeholder="role_type (e.g. frontend)"
          aria-label="Filter by role type"
          className="min-w-[200px] flex-1"
        />
        <Input
          type="text"
          value={scenario}
          onChange={(e) => setScenario(e.target.value)}
          placeholder="scenario id (e.g. hello_python_v1)"
          aria-label="Filter by scenario"
          className="min-w-[200px] flex-1"
        />
        <Button variant="outline" onClick={() => void load()} disabled={loading}>
          {loading ? "…" : "Filter"}
        </Button>
      </div>

      {error && <Alert variant="error" className="mb-3">{error}</Alert>}

      {rows.length === 0 ? (
        <EmptyState title="No ranked personas yet.">
          Train some via the Training tab to populate this leaderboard.
        </EmptyState>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>#</Th>
              <Th>Persona</Th>
              <Th>Specialty</Th>
              <Th className="text-right">Avg score</Th>
              <Th className="text-right">Runs</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.persona_id}>
                <Td>{i + 1}</Td>
                <Td>
                  <strong>{row.name}</strong>
                  <div className="font-mono text-xs text-muted-foreground">{row.persona_id}</div>
                </Td>
                <Td className="text-xs">{row.specialty || "—"}</Td>
                <Td className="text-right">{row.average_score.toFixed(2)}</Td>
                <Td className="text-right">{row.n_runs}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}
