import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, personasApi, trainingApi } from "../api";
import type { TrainingScenario } from "../api";
import { ScenarioCard } from "../components/ScenarioCard";
import { Button } from "../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { Alert, EmptyState, Page, SectionTitle, Skeleton } from "../components/ui/form-controls";
import { cn } from "../lib/utils";
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

  if (loading) {
    return (
      <Page title="Training Camp">
        <div className="grid gap-4 md:grid-cols-2" aria-busy="true" aria-label="Loading scenarios">
          <Skeleton className="h-32" />
          <Skeleton className="h-32" />
        </div>
      </Page>
    );
  }

  return (
    <Page
      title="Training Camp"
      description="Train personas on toy scenarios. Each run produces a rubric score (RUBRIC_SCORE signal) and a tagged training episode. Tournaments rank N personas on the same scenario for hiring decisions."
    >
      {error && <Alert variant="error" className="mb-3">{error}</Alert>}

      {scenarios.length === 0 ? (
        <EmptyState title="No scenarios available.">
          Add some to <code className="rounded-sm bg-muted px-1">data/training_camp/scenarios/</code> — see DESIGN §35
          for the format.
        </EmptyState>
      ) : (
        <>
          <SectionTitle>Scenarios</SectionTitle>
          <div className="grid gap-4 md:grid-cols-2">
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

      <Dialog open={!!(activeScenario && mode)} onOpenChange={(o) => !o && !busy && setMode(null)}>
        {activeScenario && mode && (
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {mode === "train" ? "Train one persona on" : "Tournament on"}{" "}
                <span className="font-mono">{activeScenario.scenario_id}</span>
              </DialogTitle>
              <DialogDescription>
                {mode === "train"
                  ? "Pick a persona. The training run uses FAST tier and disabled approval gates."
                  : "Pick at least 2 personas. Each gets its own run on the same scenario; the leaderboard ranks them by rubric score."}
              </DialogDescription>
            </DialogHeader>

            {personas.length === 0 ? (
              <p className="m-0 text-sm text-muted-foreground">No personas to train. Run setup or seed first.</p>
            ) : (
              <div className="flex max-h-80 flex-col gap-1 overflow-y-auto rounded-md border border-border p-2">
                {personas.map((p) => (
                  <label
                    key={p.persona_id}
                    className={cn(
                      "m-0 flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-sm text-foreground hover:bg-accent",
                      selectedPersonas.has(p.persona_id) && "bg-accent",
                    )}
                  >
                    <input
                      type={mode === "train" ? "radio" : "checkbox"}
                      name="persona-pick"
                      className="m-0 h-auto w-auto accent-primary"
                      checked={selectedPersonas.has(p.persona_id)}
                      onChange={() => togglePersona(p.persona_id)}
                    />
                    <strong>{p.name}</strong>
                    <span className="ml-1 font-mono text-xs text-muted-foreground">{p.persona_id}</span>
                    <span className="ml-2 text-xs text-muted-foreground">({p.specialty})</span>
                  </label>
                ))}
              </div>
            )}

            <DialogFooter>
              <Button variant="outline" onClick={() => setMode(null)} disabled={busy}>
                Cancel
              </Button>
              <Button onClick={() => void launch()} disabled={busy || selectedPersonas.size === 0}>
                {busy
                  ? "Launching…"
                  : mode === "train"
                  ? "Train"
                  : `Run tournament (${selectedPersonas.size})`}
              </Button>
            </DialogFooter>
          </DialogContent>
        )}
      </Dialog>
    </Page>
  );
}
