import type { Signal } from "../types";
import { Card } from "./ui/card";

export function ScreenshotPane({ signals }: { signals: Signal[] }) {
  const screenshots = signals.filter((s) => s.type === "SCREENSHOT_READY");

  return (
    <Card className="mb-3 p-4">
      <h2 className="m-0 text-[13px] font-semibold">Screenshots</h2>
      <div className="text-xs text-muted-foreground">UI artifacts the visual checker rendered.</div>
      {screenshots.length === 0 ? (
        <p className="mb-0 mt-2 text-xs text-muted-foreground">No screenshots yet.</p>
      ) : (
        <div className="mt-2 flex flex-col gap-1.5">
          {screenshots.map((s) => (
            <div key={s.id} className="text-xs">
              <div className="text-muted-foreground">{s.from_agent}</div>
              <div>{s.summary}</div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
