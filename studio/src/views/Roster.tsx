import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, personasApi } from "../api";
import { PersonaChatBox } from "../components/PersonaChatBox";
import { RankingsPanel } from "../components/RankingsPanel";
import type { PersonaSummary } from "../types";

type RosterTab = "all" | "rankings";

export function Roster() {
  const { id } = useParams<{ id?: string }>();
  const [tab, setTab] = useState<RosterTab>("all");
  const [personas, setPersonas] = useState<PersonaSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [episodes, setEpisodes] = useState<unknown[] | null>(null);
  const [patterns, setPatterns] = useState<{
    patterns: unknown[];
    anti_patterns: unknown[];
  } | null>(null);

  useEffect(() => {
    void loadList();
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
    <div className="page">
      <h1>Roster</h1>
      <p className="muted small">
        Your stable of agents. Personas accumulate memory across runs — episodes
        (raw), patterns (distilled), and reputation aggregates.
      </p>

      <div className="tab-row">
        <button
          className={`tab ${tab === "all" ? "tab-active" : ""}`}
          onClick={() => setTab("all")}
        >
          All personas
        </button>
        <button
          className={`tab ${tab === "rankings" ? "tab-active" : ""}`}
          onClick={() => setTab("rankings")}
        >
          Leaderboard
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {tab === "rankings" ? (
        <RankingsPanel />
      ) : (
        <div className="grid-2">
          <div>
            {personas.length === 0 ? (
              <p className="muted">
                No personas yet. They'll appear here after the first runs that
                hire them.
              </p>
            ) : (
              personas.map((p) => (
                <Link
                  to={`/roster/${p.persona_id}`}
                  key={p.persona_id}
                  style={{ display: "block", textDecoration: "none", color: "inherit" }}
                >
                  <div
                    className={`card persona-card ${id === p.persona_id ? "selected" : ""}`}
                    style={{
                      borderColor: id === p.persona_id ? "#2563eb" : undefined,
                    }}
                  >
                    <div className="name">{p.name}</div>
                    <div className="specialty">{p.specialty}</div>
                    <div className="reputation">
                      {p.n_episodes} episodes ·{" "}
                      {p.reputation?.production?.total_runs ?? 0} production runs
                      {p.reputation?.training?.graduation_status === "graduated" && (
                        <span
                          className="tag tag-graduated"
                          style={{ marginLeft: 8 }}
                        >
                          graduated
                        </span>
                      )}
                    </div>
                  </div>
                </Link>
              ))
            )}
          </div>

          <div>
            {!id ? (
              <p className="muted small">Select a persona to inspect their memory.</p>
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

  return (
    <div>
      <h2>{persona?.name ?? personaId}</h2>
      {persona?.character && (
        <div className="card">
          <div className="muted small">Character</div>
          <p style={{ margin: "4px 0 0 0" }}>{persona.character}</p>
        </div>
      )}

      {/* Phase G.0 — reputation card with graduation badge + production / training split */}
      <div className="card reputation-card">
        <div className="row" style={{ alignItems: "center" }}>
          <h3 style={{ margin: 0, flex: 1 }}>Reputation</h3>
          <span className={`tag tag-${graduation}`}>{graduation}</span>
        </div>
        <div className="grid-2" style={{ marginTop: 12 }}>
          <div>
            <div className="muted small">Production</div>
            <div className="metric">
              {reputation?.production?.total_runs ?? 0}
              <span className="metric-label"> runs</span>
            </div>
            {reputation?.production?.reviewer_first_pass_rate !== undefined && (
              <div className="small muted">
                first-pass approval:{" "}
                {(reputation.production.reviewer_first_pass_rate * 100).toFixed(0)}%
              </div>
            )}
          </div>
          <div>
            <div className="muted small">Training</div>
            <div className="metric">
              {reputation?.training?.total_runs ?? 0}
              <span className="metric-label"> runs</span>
            </div>
            <div className="small muted">
              {reputation?.active_patterns ?? 0} active patterns
            </div>
          </div>
        </div>
      </div>

      <h2>Patterns ({patterns?.patterns.length ?? 0})</h2>
      {patterns && patterns.patterns.length === 0 && (
        <p className="muted small">No distilled patterns yet.</p>
      )}
      {patterns?.patterns.map((p, i) => {
        const pat = p as {
          title: string;
          lesson: string;
          status: string;
          confidence: number;
        };
        return (
          <div className="card" key={i}>
            <div style={{ fontWeight: 600 }}>{pat.title}</div>
            <div className="small muted">{pat.lesson}</div>
            <div className="small" style={{ marginTop: 4 }}>
              <span className="tag">{pat.status}</span>{" "}
              confidence {pat.confidence?.toFixed(2)}
            </div>
          </div>
        );
      })}

      {/* Phase G.0 — anti-patterns rendered alongside patterns. Same data source
          (patterns.json) but distinct visual treatment. */}
      {patterns && patterns.anti_patterns.length > 0 && (
        <>
          <h2>Anti-patterns ({patterns.anti_patterns.length})</h2>
          {patterns.anti_patterns.map((ap, i) => {
            const a = ap as {
              title: string;
              lesson: string;
              alternative: string;
              status: string;
              confidence: number;
            };
            return (
              <div className="card anti-pattern-card" key={i}>
                <div style={{ fontWeight: 600 }}>{a.title}</div>
                <div className="small muted">{a.lesson}</div>
                {a.alternative && (
                  <div className="small" style={{ marginTop: 6 }}>
                    <strong>Instead:</strong> {a.alternative}
                  </div>
                )}
                <div className="small" style={{ marginTop: 4 }}>
                  <span className="tag tag-anti">{a.status}</span>{" "}
                  confidence {a.confidence?.toFixed(2)}
                </div>
              </div>
            );
          })}
        </>
      )}

      <h2>Recent episodes ({episodes?.length ?? 0})</h2>
      {episodes && episodes.length === 0 && (
        <p className="muted small">No episodes yet.</p>
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
          <div className="card" key={i}>
            <div className="small muted">
              {ep.timestamp?.replace("T", " ").split(".")[0]}
              {ep.training && (
                <span className="tag tag-training" style={{ marginLeft: 8 }}>
                  training
                </span>
              )}
              {ep.rubric_score !== null && ep.rubric_score !== undefined && (
                <span className="small muted" style={{ marginLeft: 8 }}>
                  rubric: {ep.rubric_score.toFixed(2)}
                </span>
              )}
            </div>
            <div>{ep.task_context}</div>
            <div className="small" style={{ marginTop: 4 }}>
              {(ep.tags ?? []).map((t) => (
                <span key={t} className="tag" style={{ marginRight: 4 }}>{t}</span>
              ))}
            </div>
          </div>
        );
      })}

      {/* Phase G.0 — chat with this persona via F.4 endpoint */}
      <PersonaChatBox personaId={personaId} />
    </div>
  );
}
