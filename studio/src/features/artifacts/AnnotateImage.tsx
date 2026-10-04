import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Button } from "../../components/ui/button";
import { regionFromPoints, submitAnnotation, type Region } from "./annotate";
import type { GalleryItem } from "./model";

interface Drag {
  /** Corners in on-screen pixels, relative to the image's top-left. */
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

type Status = { kind: "idle" } | { kind: "sending" } | { kind: "sent"; via: string } | { kind: "error"; message: string };

/**
 * Draw a rectangle on a screenshot, write a note, send it to the card (and, for an agent's
 * visual-feedback request, to that agent). The rectangle is sent in the image's own pixels,
 * encoded in the text as `[region x,y,w,h]`.
 */
export function AnnotateImage({ runId, item }: { runId: string; item: GalleryItem }) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [text, setText] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  /** On-screen pixels per image pixel's inverse: image px = screen px * scale. */
  function scale(): number {
    const img = imgRef.current;
    if (!img) return 1;
    const shown = img.getBoundingClientRect().width;
    return img.naturalWidth > 0 && shown > 0 ? img.naturalWidth / shown : 1;
  }

  function point(e: ReactPointerEvent<HTMLElement>): { x: number; y: number } {
    const rect = (imgRef.current as HTMLImageElement).getBoundingClientRect();
    return {
      x: Math.min(Math.max(e.clientX - rect.left, 0), rect.width),
      y: Math.min(Math.max(e.clientY - rect.top, 0), rect.height),
    };
  }

  function onPointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    if (!imgRef.current) return;
    const p = point(e);
    setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
    setDrawing(true);
    setStatus({ kind: "idle" });
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* no pointer capture (tests): the window still delivers moves over the overlay */
    }
  }

  function onPointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    if (!drawing || !imgRef.current) return;
    const p = point(e);
    setDrag((d) => (d ? { ...d, x1: p.x, y1: p.y } : d));
  }

  function onPointerUp() {
    setDrawing(false);
    setDrag((d) => (d && Math.abs(d.x1 - d.x0) < 3 && Math.abs(d.y1 - d.y0) < 3 ? null : d));
  }

  const region: Region | null = drag
    ? regionFromPoints({ x: drag.x0 * scale(), y: drag.y0 * scale() }, { x: drag.x1 * scale(), y: drag.y1 * scale() })
    : null;

  async function send() {
    setStatus({ kind: "sending" });
    try {
      const result = await submitAnnotation(runId, item, region, text);
      setStatus({ kind: "sent", via: result.sent.join(" and ") });
      setText("");
      setDrag(null);
    } catch (err) {
      setStatus({ kind: "error", message: err instanceof Error ? err.message : "send failed" });
    }
  }

  const where = [item.taskId ? `card ${item.taskId}` : null, item.requestId ? "the agent waiting on this screenshot" : null]
    .filter((s): s is string => s !== null)
    .join(" and ");

  return (
    <div className="flex flex-col gap-3">
      {item.question && (
        <p className="m-0 rounded-md border border-border bg-muted p-2 text-sm">
          The agent asks: {item.question}
        </p>
      )}
      <div
        data-testid="annotate-surface"
        className="relative inline-block max-w-full cursor-crosshair touch-none select-none self-start"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
        <img ref={imgRef} src={item.url} alt={`Annotate ${item.name}`} draggable={false} className="block max-h-[60vh] max-w-full" />
        {drag && (
          <div
            data-testid="annotate-rect"
            className="pointer-events-none absolute border-2 border-primary bg-primary/20"
            // Computed from the live drag; no token or utility class can express it.
            style={{
              left: Math.min(drag.x0, drag.x1),
              top: Math.min(drag.y0, drag.y1),
              width: Math.abs(drag.x1 - drag.x0),
              height: Math.abs(drag.y1 - drag.y0),
            }}
          />
        )}
      </div>
      <p className="m-0 text-xs text-muted-foreground" data-testid="annotate-region">
        {region ? `Region ${region.x},${region.y} ${region.w}x${region.h} px` : "Drag on the image to mark a region (optional)."}
      </p>
      <label className="flex flex-col gap-1 text-sm">
        Note
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          className="rounded-md border border-input bg-background p-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </label>
      <div className="flex items-center gap-3">
        <Button onClick={() => void send()} disabled={text.trim() === "" || status.kind === "sending"}>
          {status.kind === "sending" ? "Sending…" : "Send note"}
        </Button>
        <span className="text-xs text-muted-foreground">
          Goes to {where || "the run as a comment"}.
        </span>
      </div>
      {status.kind === "sent" && (
        <p role="status" className="m-0 text-sm text-status-complete">Sent as {status.via}.</p>
      )}
      {status.kind === "error" && (
        <p role="alert" className="m-0 text-sm text-destructive">Could not send: {status.message}</p>
      )}
    </div>
  );
}
