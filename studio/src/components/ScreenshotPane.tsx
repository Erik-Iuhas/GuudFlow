import type { Signal } from "../types";

export function ScreenshotPane({ signals }: { signals: Signal[] }) {
  const screenshots = signals.filter((s) => s.type === "SCREENSHOT_READY");

  return (
    <div className="card">
      <h2 style={{ margin: 0, fontSize: 13, fontWeight: 600 }}>Screenshots</h2>
      <div className="muted small">UI artifacts the visual checker rendered.</div>
      {screenshots.length === 0 ? (
        <p className="muted small" style={{ marginTop: 8 }}>
          No screenshots yet.
        </p>
      ) : (
        <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
          {screenshots.map((s) => (
            <div key={s.id} className="small">
              <div className="muted">{s.from_agent}</div>
              <div>{s.summary}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
