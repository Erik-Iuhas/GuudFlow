import { useEffect, useState } from "react";
import { personasApi, ApiError } from "../api";
import type { PersonaSummary } from "../types";
import { avatarFor } from "../lib/avatar";
import { cn } from "../lib/utils";
import { DeskFilesSection } from "./DeskFilesSection";
import { Badge } from "./ui/badge";
import { Card } from "./ui/card";
import { Alert } from "./ui/form-controls";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "./ui/sheet";

interface Props {
  personaId: string;
  onClose: () => void;
}

export function PersonaSlideOver({ personaId, onClose }: Props) {
  const [persona, setPersona] = useState<PersonaSummary | null>(null);
  const [episodes, setEpisodes] = useState<unknown[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void load();
  }, [personaId]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [p, eps] = await Promise.all([
        personasApi.get(personaId),
        personasApi.episodes(personaId, 5),
      ]);
      setPersona(p);
      setEpisodes(eps);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setLoading(false);
    }
  }

  const av = avatarFor(personaId);
  const rep = persona?.reputation;
  const totalRuns = (rep?.production?.total_runs ?? 0) + (rep?.training?.total_runs ?? 0);
  const winRate = rep?.production?.reviewer_first_pass_rate;

  const statLabel = "text-xs text-muted-foreground";

  return (
    <Sheet open onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-[480px] max-w-full gap-0 overflow-y-auto p-0" aria-describedby={undefined}>
        <SheetHeader className="shrink-0 border-b border-border px-5 py-4">
          <div className="flex items-center gap-3 pr-8">
            {/* The gradient is generated per persona at runtime, so it is handed over
                as the `--avatar-bg` CSS variable and painted by a class. */}
            <div
              className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-[image:var(--avatar-bg)] text-lg font-bold text-white"
              style={{ "--avatar-bg": av.gradient } as React.CSSProperties}
              aria-hidden
            >
              {av.initials}
            </div>
            <div>
              <SheetTitle className="text-base">{persona?.name ?? personaId}</SheetTitle>
              <div className={statLabel}>{persona?.specialty}</div>
            </div>
          </div>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading && <div className="text-xs text-muted-foreground">Loading…</div>}
          {error && <Alert variant="error" className="mb-3">{error}</Alert>}

          {persona && (
            <>
              {/* Goal */}
              {persona.goal ? (
                <Card className="mb-3 p-4">
                  <div className={cn(statLabel, "mb-0.5")}>Goal</div>
                  <div className="text-sm">{persona.goal}</div>
                </Card>
              ) : (
                <div className="mb-3 text-xs text-muted-foreground">No goal set.</div>
              )}

              {/* Reputation snapshot */}
              <Card className="mb-3 p-4">
                <div className={cn(statLabel, "mb-1.5")}>Reputation</div>
                <div className="flex flex-wrap items-center gap-4">
                  <div>
                    <div className="font-semibold">{totalRuns}</div>
                    <div className={statLabel}>total runs</div>
                  </div>
                  <div>
                    <div className="font-semibold">{persona.n_episodes}</div>
                    <div className={statLabel}>episodes</div>
                  </div>
                  {winRate !== undefined && (
                    <div>
                      <div className="font-semibold">{(winRate * 100).toFixed(0)}%</div>
                      <div className={statLabel}>first-pass rate</div>
                    </div>
                  )}
                  {rep?.training?.graduation_status === "graduated" && (
                    <Badge variant="complete">graduated</Badge>
                  )}
                </div>
              </Card>

              {/* Style tendencies */}
              {persona.style_tendencies && Object.keys(persona.style_tendencies).length > 0 && (
                <div className="mb-3">
                  <div className={cn(statLabel, "mb-1")}>Style tendencies</div>
                  {Object.entries(persona.style_tendencies)
                    .sort(([, a], [, b]) => b - a)
                    .slice(0, 5)
                    .map(([key, val]) => (
                      <div key={key} className="mb-0.5 flex items-center justify-between text-xs">
                        <span>{key.replace(/_/g, " ")}</span>
                        <span className="text-muted-foreground">{(val * 100).toFixed(0)}%</span>
                      </div>
                    ))}
                </div>
              )}
            </>
          )}

          {/* Last 5 episodes */}
          {episodes && episodes.length > 0 && (
            <div>
              <div className={cn(statLabel, "mb-1.5")}>Recent episodes</div>
              {episodes.slice(-5).reverse().map((e, i) => {
                const ep = e as {
                  episode_id?: string;
                  task_context?: string;
                  tags?: string[];
                  timestamp?: string;
                  training?: boolean;
                  rubric_score?: number | null;
                };
                return (
                  <Card key={i} className="mb-1.5 px-3 py-2">
                    <div className={statLabel}>
                      {ep.timestamp?.replace("T", " ").split(".")[0]}
                      {ep.training && (
                        <Badge variant="waiting" className="ml-1.5">training</Badge>
                      )}
                    </div>
                    <div className="text-xs">{ep.task_context}</div>
                  </Card>
                );
              })}
            </div>
          )}
          {episodes && episodes.length === 0 && (
            <div className="text-xs text-muted-foreground">No episodes yet.</div>
          )}

          <DeskFilesSection personaId={personaId} />
        </div>
      </SheetContent>
    </Sheet>
  );
}
