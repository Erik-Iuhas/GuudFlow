import type { TrainingScenario } from "../api";

/**
 * Phase G.4 — one row in the Training tab's scenario picker. Shows
 * goal + role types + auto-check count + the action buttons.
 */
export function ScenarioCard({
  scenario,
  onTrain,
  onTournament,
  selected = false,
}: {
  scenario: TrainingScenario;
  onTrain: (s: TrainingScenario) => void;
  onTournament: (s: TrainingScenario) => void;
  selected?: boolean;
}) {
  return (
    <div
      className={`card scenario-card ${selected ? "selected" : ""}`}
      style={{ borderColor: selected ? "#2563eb" : undefined }}
    >
      <div className="row" style={{ alignItems: "center", gap: 8 }}>
        <strong className="mono">{scenario.scenario_id}</strong>
        <span className="tag small">{scenario.project_type}</span>
        <span className="muted small" style={{ marginLeft: "auto" }}>
          {scenario.n_auto_checks} checks
        </span>
      </div>
      <p className="small" style={{ margin: "8px 0" }}>
        {scenario.goal}
      </p>
      <div className="small muted" style={{ marginBottom: 8 }}>
        roles: {scenario.expected_role_types.join(", ")}
      </div>
      <div className="row" style={{ gap: 8 }}>
        <button onClick={() => onTrain(scenario)}>Train one</button>
        <button onClick={() => onTournament(scenario)}>
          Tournament
        </button>
      </div>
    </div>
  );
}
