import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, personasApi, trainingApi } from "../api";
import type { TrainingScenario } from "../api";
import { ScenarioCard } from "../components/ScenarioCard";
import type { PersonaSummary } from "../types";

/**
 * Phase G.4 — Training Camp top-level view. Consumes three of the
 * four /api/training/* endpoints (the fourth, tournament-result
 * polling, lives in views/Tournament.tsx).
 *
 * Two launchers:
 *  - "Train one" — POST /api/training/run with one persona × one
 *    scenario, navigates to the resulting Run view.
 *  - "Tournament" — POST /api/training/tournament with N personas ×
 *    one scenario, navigates to the Tournament leaderboard view.
 */
export function Training() {
  const [scenarios, setScenarios] = useState<TrainingScenario[]>([]);
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeScenario, setActiveScenario] = useState<TrainingScenario | null>(null);
  const [mode, setMode] = useState<"train" | "tournament" | null>(null);
  const [selectedPersonas, setSelectedPersonas] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    void load();
  }, []);

  async function load() {
    try {
      const [s, p] = await Promise.all([
        trainingApi.listScenarios(),
        personasApi.list(),
      ]);
      setScenarios(s);
      setPersonas(p);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoading(false);
    }
  }

  function startTrain(s: TrainingScenario) {
    setActiveScenario(s);
    setMode("train");
    setSelectedPersonas(new Set());
  }

  function startTournament(s: TrainingScenario) {
    setActiveScenario(s);
    setMode("tournament");
    setSelectedPersonas(new Set());
  }

  function togglePersona(id: string) {
    setSelectedPersonas((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      // Single-select for "train" mode.
      if (mode === "train" && next.size > 1) {
        next.clear();
        next.add(id);
      }
      return next;
    });
  }

  async function launch() {
    if (!activeScenario || selectedPersonas.size === 0) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "train") {
        const personaId = Array.from(selectedPersonas)[0];
        const r = await trainingApi.run({
          persona_id: personaId,
          scenario_id: activeScenario.scenario_id,
        });
        navigate(`/runs/${r.run_id}`);
      } else if (mode === "tournament") {
        if (selectedPersonas.size < 2) {
          setError("Tournaments need at least 2 personas");
          setBusy(false);
          return;
        }
        const r = await trainingApi.tournament({
          scenario_id: activeScenario.scenario_id,
          persona_ids: Array.from(selectedPersonas),
        });
        navigate(`/training/tournaments/${r.tournament_id}`);
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className="page">Loading…</div>;

  return (
    <div className="page">
      <h1>Training Camp</h1>
      <p className="muted small">
        Train personas on toy scenarios. Each run produces a rubric
        score (RUBRIC_SCORE signal) and a tagged training episode.
        Tournaments rank N personas on the same scenario for hiring
        decisions.
      </p>

      {error && <div className="error">{error}</div>}

      {scenarios.length === 0 ? (
        <div className="card">
          <p className="muted">
            No scenarios available. Add some to{" "}
            <code>data/training_camp/scenarios/</code> — see DESIGN §35
            for the format.
          </p>
        </div>
      ) : (
        <>
          <h2>Scenarios</h2>
          <div className="grid-2">
            {scenarios.map((s) => (
              <ScenarioCard
                key={s.scenario_id}
                scenario={s}
                onTrain={startTrain}
                onTournament={startTournament}
                selected={activeScenario?.scenario_id === s.scenario_id}
              />
            ))}
          </div>
        </>
      )}

      {activeScenario && mode && (
        <div
          className="modal-backdrop"
          onClick={() => !busy && setMode(null)}
        >
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>
              {mode === "train" ? "Train one persona on" : "Tournament on"}{" "}
              <span className="mono">{activeScenario.scenario_id}</span>
            </h3>
            <p className="small muted">
              {mode === "train"
                ? "Pick a persona. The training run uses FAST tier and disabled approval gates."
                : "Pick at least 2 personas. Each gets its own run on the same scenario; the leaderboard ranks them by rubric score."}
            </p>

            {personas.length === 0 ? (
              <p className="muted">No personas to train. Run setup or seed first.</p>
            ) : (
              <div className="persona-pick-list">
                {personas.map((p) => (
                  <label
                    key={p.persona_id}
                    className={`persona-pick ${
                      selectedPersonas.has(p.persona_id) ? "selected" : ""
                    }`}
                  >
                    <input
                      type={mode === "train" ? "radio" : "checkbox"}
                      name="persona-pick"
                      checked={selectedPersonas.has(p.persona_id)}
                      onChange={() => togglePersona(p.persona_id)}
                    />
                    <strong>{p.name}</strong>
                    <span className="small muted mono" style={{ marginLeft: 6 }}>
                      {p.persona_id}
                    </span>
                    <span className="small muted" style={{ marginLeft: 8 }}>
                      ({p.specialty})
                    </span>
                  </label>
                ))}
              </div>
            )}

            <div className="row" style={{ marginTop: 12, gap: 8 }}>
              <button
                className="primary"
                onClick={() => void launch()}
                disabled={busy || selectedPersonas.size === 0}
              >
                {busy
                  ? "Launching…"
                  : mode === "train"
                  ? "Train"
                  : `Run tournament (${selectedPersonas.size})`}
              </button>
              <button onClick={() => setMode(null)} disabled={busy}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
