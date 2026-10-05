import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { agentsApi, ApiError, personasApi } from "../api";
import type { CatalogStatus } from "../api";
import { PersonaChatBox } from "../components/PersonaChatBox";
import { PersonaSlideOver } from "../components/PersonaSlideOver";
import { RankingsPanel } from "../components/RankingsPanel";
import { WorkerCard } from "../components/WorkerCard";
import { Badge } from "../components/ui/badge";
import { Card } from "../components/ui/card";
import { Alert, linkClass, Page, SectionTitle } from "../components/ui/form-controls";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import type { PersonaSummary } from "../types";

type RosterTab = "all" | "rankings";

export function Roster() {
  const { id } = useParams<{ id?: string }>();
  const [tab, setTab] = useState<RosterTab>("all");
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [catalog, setCatalog] = useState<CatalogStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [episodes, setEpisodes] = useState<unknown[] | null>(null);
  const [patterns, setPatterns] = useState<{
    patterns: unknown[];
    anti_patterns: unknown[];
  } | null>(null);
  const [slideOverPersonaId, setSlideOverPersonaId] = useState<string | null>(null);

  useEffect(() => {
    void loadList();
    // Catalog status is best-effort — failures shouldn't block the
    // rest of the roster view.
    agentsApi.catalogStatus().then(setCatalog).catch(() => {});
  }, []);

  useEffect(() => {
    if (id) void loadDetails(id);
    else {
      setEpisodes(null);
      setPatterns(null);
    }
  }, [id]);

  async function loadList() {
    try {
      setPersonas(await personasApi.list());
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function loadDetails(personaId: string) {
    try {
      const [eps, pats] = await Promise.all([
        personasApi.episodes(personaId, 25),
        personasApi.patterns(personaId),
      ]);
      setEpisodes(eps);
      setPatterns(pats);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <Page
      title="Roster"
      description="Your stable of agents. Personas accumulate memory across runs — episodes (raw), patterns (distilled), and reputation aggregates."
    >
      <Tabs value={tab} onValueChange={(v) => setTab(v as RosterTab)}>
        <TabsList aria-label="Roster sections">
          <TabsTrigger value="all">All personas</TabsTrigger>
          <TabsTrigger value="rankings">Leaderboard</TabsTrigger>
        </TabsList>
      </Tabs>

      {error && <Alert variant="error" className="mt-3">{error}</Alert>}

      <div className="mt-3">
        {tab === "rankings" ? (
          <RankingsPanel />
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="flex flex-col gap-3">
              {personas.length === 0 ? (
                <RosterEmptyState catalog={catalog} />
              ) : (
                personas.map((p) => (
                  <WorkerCard
                    key={p.persona_id}
                    agent={{
                      role: p.specialty || p.name,
                      persona_id: p.persona_id,
                    }}
                    mode="roster"
                    onOpenPersona={(pid) => setSlideOverPersonaId(pid)}
                  />
                ))
              )}
            </div>

            <div>
              {!id ? (
                <p className="m-0 text-sm text-muted-foreground">Select a persona to inspect their memory.</p>
              ) : (
                <PersonaDetail
                  personaId={id}
                  episodes={episodes}
                  patterns={patterns}
                  persona={personas.find((p) => p.persona_id === id) ?? null}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {slideOverPersonaId && (
        <PersonaSlideOver
          personaId={slideOverPersonaId}
          onClose={() => setSlideOverPersonaId(null)}
        />
      )}
    </Page>
  );
}

function PersonaDetail({
  personaId,
  episodes,
  patterns,
  persona,
}: {
  personaId: string;
  episodes: unknown[] | null;
  patterns: { patterns: unknown[]; anti_patterns: unknown[] } | null;
  persona: PersonaSummary | null;
}) {
  const reputation = persona?.reputation;
  const graduation = reputation?.training?.graduation_status ?? "ungraduated";
  const metric = "text-2xl font-semibold";
  const metricLabel = "text-sm font-normal text-muted-foreground";

  return (
    <div>
      <SectionTitle className="mt-0">{persona?.name ?? personaId}</SectionTitle>
      {persona?.character && (
        <Card className="mb-3 p-4">
          <div className="text-xs text-muted-foreground">Character</div>
          <p className="m-0 mt-1 text-sm">{persona.character}</p>
        </Card>
      )}

      {/* Phase G.0 — reputation card with graduation badge + production / training split */}
      <Card className="mb-3 bg-muted p-4">
        <div className="flex items-center">
          <h3 className="m-0 flex-1 text-base font-semibold">Reputation</h3>
          <Badge variant={graduation === "graduated" ? "complete" : graduation === "training" ? "blocked" : "secondary"}>
            {graduation}
          </Badge>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-4">
          <div>
            <div className="text-xs text-muted-foreground">Production</div>
            <div className={metric}>
              {reputation?.production?.total_runs ?? 0}
              <span className={metricLabel}> runs</span>
            </div>
            {reputation?.production?.reviewer_first_pass_rate !== undefined && (
              <div className="text-xs text-muted-foreground">
                first-pass approval:{" "}
                {(reputation.production.reviewer_first_pass_rate * 100).toFixed(0)}%
              </div>
            )}
          </div>
          <div>
            <div className="text-xs text-muted-foreground">Training</div>
            <div className={metric}>
              {reputation?.training?.total_runs ?? 0}
              <span className={metricLabel}> runs</span>
            </div>
            <div className="text-xs text-muted-foreground">
              {reputation?.active_patterns ?? 0} active patterns
            </div>
          </div>
        </div>
      </Card>

      <SectionTitle>Patterns ({patterns?.patterns.length ?? 0})</SectionTitle>
      {patterns && patterns.patterns.length === 0 && (
        <p className="m-0 text-xs text-muted-foreground">No distilled patterns yet.</p>
      )}
      {patterns?.patterns.map((p, i) => {
        const pat = p as {
          title: string;
          lesson: string;
          status: string;
          confidence: number;
        };
        return (
          <Card className="mb-3 p-4" key={i}>
            <div className="font-semibold">{pat.title}</div>
            <div className="text-xs text-muted-foreground">{pat.lesson}</div>
            <div className="mt-1 text-xs">
              <Badge variant="secondary">{pat.status}</Badge>{" "}
              confidence {pat.confidence?.toFixed(2)}
            </div>
          </Card>
        );
      })}

      {/* Phase G.0 — anti-patterns rendered alongside patterns. Same data source
          (patterns.json) but distinct visual treatment. */}
      {patterns && patterns.anti_patterns.length > 0 && (
        <>
          <SectionTitle>Anti-patterns ({patterns.anti_patterns.length})</SectionTitle>
          {patterns.anti_patterns.map((ap, i) => {
            const a = ap as {
              title: string;
              lesson: string;
              alternative: string;
              status: string;
              confidence: number;
            };
            return (
              <Card className="mb-3 border-l-[3px] border-l-status-failed p-4" key={i}>
                <div className="font-semibold">{a.title}</div>
                <div className="text-xs text-muted-foreground">{a.lesson}</div>
                {a.alternative && (
                  <div className="mt-1.5 text-xs">
                    <strong>Instead:</strong> {a.alternative}
                  </div>
                )}
                <div className="mt-1 text-xs">
                  <Badge variant="failed">{a.status}</Badge>{" "}
                  confidence {a.confidence?.toFixed(2)}
                </div>
              </Card>
            );
          })}
        </>
      )}

      <SectionTitle>Recent episodes ({episodes?.length ?? 0})</SectionTitle>
      {episodes && episodes.length === 0 && (
        <p className="m-0 text-xs text-muted-foreground">No episodes yet.</p>
      )}
      {episodes?.slice(-10).reverse().map((e, i) => {
        const ep = e as {
          episode_id?: string;
          task_context?: string;
          tags?: string[];
          timestamp?: string;
          training?: boolean;
          rubric_score?: number | null;
        };
        return (
          <Card className="mb-3 p-4" key={i}>
            <div className="text-xs text-muted-foreground">
              {ep.timestamp?.replace("T", " ").split(".")[0]}
              {ep.training && (
                <Badge variant="blocked" className="ml-2">
                  training
                </Badge>
              )}
              {ep.rubric_score !== null && ep.rubric_score !== undefined && (
                <span className="ml-2 text-xs text-muted-foreground">
                  rubric: {ep.rubric_score.toFixed(2)}
                </span>
              )}
            </div>
            <div className="text-sm">{ep.task_context}</div>
            <div className="mt-1 flex flex-wrap gap-1 text-xs">
              {(ep.tags ?? []).map((t) => (
                <Badge key={t} variant="secondary">{t}</Badge>
              ))}
            </div>
          </Card>
        );
      })}

      {/* Phase G.0 — chat with this persona via F.4 endpoint */}
      <PersonaChatBox personaId={personaId} />
    </div>
  );
}


/**
 * Phase G.2 — first-timer's empty Roster view. Surfaces the
 * catalog-status snapshot so the user sees the system has 16
 * archetypes available even before any persona is hired.
 */
function RosterEmptyState({ catalog }: { catalog: CatalogStatus | null }) {
  const metric = "text-2xl font-semibold";
  return (
    <div className="flex flex-col gap-3">
      <Card className="p-4">
        <h3 className="m-0 mb-2 text-base font-semibold">No personas yet</h3>
        <p className="mb-2 mt-0 text-sm text-muted-foreground">
          Personas appear here after the first runs that hire them.
          The seed personas (Mike, Sarah, Alex) ship with the install
          script — if you don't see them, run{" "}
          <code className="rounded-sm bg-muted px-1">python scripts/seed_data.py</code>.
        </p>
        <p className="m-0 text-sm">
          New to GuudFlow? <Link to="/projects" className={linkClass}>Try the demo</Link> on
          the Projects page — it hires the seed personas for you.
        </p>
      </Card>

      {catalog && (
        <Card className="p-4">
          <h3 className="m-0 mb-2 text-base font-semibold">Available archetypes</h3>
          <p className="mb-3 mt-0 text-xs text-muted-foreground">
            The meta-agent picks from these when proposing a team.
            Shipped archetypes have concrete classes; described-only
            archetypes are prompt-defined from the YAML catalog.
          </p>
          <div className="flex flex-wrap gap-4">
            <div>
              <div className={metric}>{catalog.n_total}</div>
              <div className="text-xs text-muted-foreground">total archetypes</div>
            </div>
            <div>
              <div className={metric}>{catalog.n_shipped}</div>
              <div className="text-xs text-muted-foreground">shipped (concrete classes)</div>
            </div>
            <div>
              <div className={metric}>{catalog.n_described_only}</div>
              <div className="text-xs text-muted-foreground">described-only (YAML)</div>
            </div>
            <div>
              <div className={metric}>{catalog.n_scenarios}</div>
              <div className="text-xs text-muted-foreground">training scenarios</div>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
