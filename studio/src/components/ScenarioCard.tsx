import type { TrainingScenario } from "../api";
import { cn } from "../lib/utils";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

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
    <Card className={cn("p-4 transition-colors", selected && "border-primary")}>
      <div className="flex items-center gap-2">
        <strong className="font-mono">{scenario.scenario_id}</strong>
        <Badge variant="secondary">{scenario.project_type}</Badge>
        <span className="ml-auto text-xs text-muted-foreground">{scenario.n_auto_checks} checks</span>
      </div>
      <p className="my-2 text-sm">{scenario.goal}</p>
      <div className="mb-2 text-xs text-muted-foreground">
        roles: {scenario.expected_role_types.join(", ")}
      </div>
      <div className="flex gap-2">
        <Button variant="outline" onClick={() => onTrain(scenario)}>
          Train one
        </Button>
        <Button variant="outline" onClick={() => onTournament(scenario)}>
          Tournament
        </Button>
      </div>
    </Card>
  );
}
