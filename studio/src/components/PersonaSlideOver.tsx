import { useEffect, useState } from "react";
import { personasApi, ApiError } from "../api";
import type { PersonaSummary } from "../types";
import { avatarFor } from "../lib/avatar";
import { DeskFilesSection } from "./DeskFilesSection";

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

  // Close on Escape
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const av = avatarFor(personaId);
  const rep = persona?.reputation;
  const totalRuns = (rep?.production?.total_runs ?? 0) + (rep?.training?.total_runs ?? 0);
  const winRate = rep?.production?.reviewer_first_pass_rate;

  return (
    <>
      <div className="slide-over-backdrop" onClick={onClose} />
      <div className="slide-over" role="dialog" aria-modal>
        <div className="slide-over-header">
          <div className="row" style={{ gap: 12, alignItems: "center" }}>
            <div
              className="worker-avatar"
              style={{ background: av.gradient, width: 48, height: 48, fontSize: 18 }}
            >
              {av.initials}
            </div>
            <div>
              <div style={{ fontWeight: 600, fontSize: 16 }}>
                {persona?.name ?? personaId}
              </div>
              <div className="small muted">{persona?.specialty}</div>
            </div>
          </div>
          <button className="close-btn" onClick={onClose} title="Close">×</button>
        </div>

        <div className="slide-over-body">
          {loading && <div className="muted small">Loading…</div>}
          {error && <div className="error">{error}</div>}

          {persona && (
            <>
              {/* Goal */}
              {persona.goal ? (
                <div className="card" style={{ marginBottom: 12 }}>
                  <div className="small muted" style={{ marginBottom: 2 }}>Goal</div>
                  <div className="small">{persona.goal}</div>
                </div>
              ) : (
                <div className="muted small" style={{ marginBottom: 12 }}>No goal set.</div>
              )}

              {/* Reputation snapshot */}
              <div className="card" style={{ marginBottom: 12 }}>
                <div className="small muted" style={{ marginBottom: 6 }}>Reputation</div>
                <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
                  <div>
                    <div style={{ fontWeight: 600 }}>{totalRuns}</div>
                    <div className="small muted">total runs</div>
                  </div>
                  <div>
                    <div style={{ fontWeight: 600 }}>{persona.n_episodes}</div>
                    <div className="small muted">episodes</div>
                  </div>
                  {winRate !== undefined && (
                    <div>
                      <div style={{ fontWeight: 600 }}>{(winRate * 100).toFixed(0)}%</div>
                      <div className="small muted">first-pass rate</div>
                    </div>
                  )}
                  {rep?.training?.graduation_status === "graduated" && (
                    <span className="tag tag-graduated" style={{ alignSelf: "center" }}>
                      graduated
                    </span>
                  )}
                </div>
              </div>

              {/* Style tendencies */}
              {persona.style_tendencies && Object.keys(persona.style_tendencies).length > 0 && (
                <div style={{ marginBottom: 12 }}>
                  <div className="small muted" style={{ marginBottom: 4 }}>Style tendencies</div>
                  {Object.entries(persona.style_tendencies)
                    .sort(([, a], [, b]) => b - a)
                    .slice(0, 5)
                    .map(([key, val]) => (
                      <div key={key} className="small row" style={{ justifyContent: "space-between", marginBottom: 2 }}>
                        <span>{key.replace(/_/g, " ")}</span>
                        <span className="muted">{(val * 100).toFixed(0)}%</span>
                      </div>
                    ))}
                </div>
              )}
            </>
          )}

          {/* Last 5 episodes */}
          {episodes && episodes.length > 0 && (
            <div>
              <div className="small muted" style={{ marginBottom: 6 }}>Recent episodes</div>
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
                  <div className="card" key={i} style={{ marginBottom: 6, padding: "8px 12px" }}>
                    <div className="small muted">
                      {ep.timestamp?.replace("T", " ").split(".")[0]}
                      {ep.training && (
                        <span className="tag tag-training" style={{ marginLeft: 6 }}>training</span>
                      )}
                    </div>
                    <div className="small">{ep.task_context}</div>
                  </div>
                );
              })}
            </div>
          )}
          {episodes && episodes.length === 0 && (
            <div className="muted small">No episodes yet.</div>
          )}

          <DeskFilesSection personaId={personaId} />
        </div>
      </div>
    </>
  );
}
