import type { Signal } from "../types";

export function BoardFeed({ signals }: { signals: Signal[] }) {
  // Filter out quip-flavour signals — they have their own pane.
  const filtered = signals.filter((s) => !isQuip(s));

  return (
    <div className="feed">
      {filtered.length === 0 && (
        <p className="muted small">no signals yet — waiting for the run to start…</p>
      )}
      {filtered.map((s) => (
        <SignalRow key={s.id} signal={s} />
      ))}
    </div>
  );
}

function SignalRow({ signal }: { signal: Signal }) {
  const time = signal.timestamp?.replace("T", " ").split(".")[0]?.slice(11) ?? "";
  const kind = classify(signal);
  return (
    <div className={`signal ${kind}`}>
      <div className="sig-head">
        <span className="mono">{time}</span>
        <span className={`tag tag-status-${statusClass(signal.type)}`}>
          {signal.type}
        </span>
        {signal.from_agent && (
          <span className="muted small">{signal.from_agent}</span>
        )}
        {signal.iteration > 0 && (
          <span className="muted small">iter {signal.iteration}</span>
        )}
      </div>
      <div className="sig-summary">{signal.summary}</div>
      {signal.detail && (
        <pre>{signal.detail}</pre>
      )}
    </div>
  );
}

function classify(s: Signal): string {
  if (s.from_user) return "user";
  if (s.type === "PROTOCOL") return "protocol";
  return "";
}

function statusClass(type: string): string {
  if (type === "COMPLETE" || type === "APPROVED") return "complete";
  if (type === "FAILED" || type === "REJECTED") return "failed";
  if (type === "STARTED" || type === "THINKING") return "running";
  return "queued";
}

function isQuip(s: Signal): boolean {
  // Quips are a separate channel (DESIGN §23) but the runner doesn't tag
  // them on the main board today. Reserved hook for when they arrive
  // here too.
  return false;
}
