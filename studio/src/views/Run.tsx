import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, connectRunStream, runsApi } from "../api";
import { BoardFeed } from "../components/BoardFeed";
import { CommentBox } from "../components/CommentBox";
import { GitLogTab } from "../components/GitLogTab";
import { QuipFeed } from "../components/QuipFeed";
import { ScreenshotPane } from "../components/ScreenshotPane";
import type { RunMeta, Signal } from "../types";

export function Run() {
  const { runId } = useParams<{ runId: string }>();
  const [meta, setMeta] = useState<RunMeta | null>(null);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!runId) return;
    void loadMeta();

    const ws = connectRunStream(
      runId,
      (msg) => {
        if (msg.type === "snapshot" && msg.signals) {
          setSignals(msg.signals);
          setConnected(true);
        } else if (msg.type === "signal" && msg.signal) {
          setSignals((prev) => [...prev, msg.signal as Signal]);
        } else if (msg.type === "error") {
          setError("run not found or finished");
        }
      },
      () => setConnected(false),
    );
    wsRef.current = ws;
    return () => {
      ws.close();
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  async function loadMeta() {
    if (!runId) return;
    try {
      const m = await runsApi.get(runId);
      setMeta(m);
    } catch (e) {
      // Run might still be active and not yet flushed to disk; that's fine.
      if (e instanceof ApiError && e.status !== 404) {
        setError(e.detail);
      }
    }
  }

  if (!runId) return null;

  return (
    <div className="page" style={{ maxWidth: "100%" }}>
      <div className="run-layout">
        <div className="run-header">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <h1 style={{ marginBottom: 0 }}>
                Run <span className="mono">{runId}</span>
              </h1>
              {meta && (
                <span className="muted small">
                  project <Link to={`/projects/${meta.project_id}`}>{meta.project_id}</Link>
                  {" — "}
                  <span className={`tag tag-status-${meta.status}`}>{meta.status}</span>
                </span>
              )}
              {!meta && <span className="muted small">live (no on-disk meta yet)</span>}
            </div>
            <div className="row small muted">
              <span className={connected ? "tag tag-status-running" : "tag"}>
                {connected ? "● live" : "○ disconnected"}
              </span>
              <span>{signals.length} signals</span>
            </div>
          </div>
          {error && <div className="error">{error}</div>}
        </div>

        <div className="run-board">
          <BoardFeed signals={signals} />
        </div>

        <div className="run-side">
          <QuipFeed signals={signals} />
          <ScreenshotPane signals={signals} />
          <GitLogTab signals={signals} />
        </div>

        <div className="run-comments">
          <CommentBox runId={runId} />
        </div>
      </div>
    </div>
  );
}
