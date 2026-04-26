import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, personasApi } from "../api";
import type { PersonaSummary } from "../types";

export function Roster() {
  const { id } = useParams<{ id?: string }>();
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
      {error && <div className="error">{error}</div>}

      <div className="grid-2">
        <div>
          {personas.length === 0 ? (
            <p className="muted">No personas yet. They'll appear here after the first runs that hire them.</p>
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
  return (
    <div>
      <h2>{persona?.name ?? personaId}</h2>
      {persona?.character && (
        <div className="card">
          <div className="muted small">Character</div>
          <p style={{ margin: "4px 0 0 0" }}>{persona.character}</p>
        </div>
      )}

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
        };
        return (
          <div className="card" key={i}>
            <div className="small muted">
              {ep.timestamp?.replace("T", " ").split(".")[0]}
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
    </div>
  );
}
