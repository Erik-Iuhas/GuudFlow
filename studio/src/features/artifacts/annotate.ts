import { postCardComment, postRunComment, postVisualFeedback } from "./api";
import type { GalleryItem } from "./model";

/** A rectangle in the image's own pixels. */
export interface Region {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** `[region x,y,w,h] text`, or the bare text when no rectangle was drawn. */
export function formatRegionText(region: Region | null, text: string): string {
  const body = text.trim();
  if (!region) return body;
  return `[region ${region.x},${region.y},${region.w},${region.h}] ${body}`;
}

/** Normalize two drag corners (any direction) into a rounded region. */
export function regionFromPoints(a: { x: number; y: number }, b: { x: number; y: number }): Region {
  const x = Math.round(Math.min(a.x, b.x));
  const y = Math.round(Math.min(a.y, b.y));
  return { x, y, w: Math.round(Math.abs(a.x - b.x)), h: Math.round(Math.abs(a.y - b.y)) };
}

export interface SubmitResult {
  /** Where the note went, for the confirmation line. */
  sent: ("card comment" | "visual feedback" | "run comment")[];
}

/**
 * Send an annotation. A card comment when the artifact belongs to a card; visual feedback when
 * it answers an agent's `request_visual_feedback`; both when both apply. An artifact with
 * neither falls back to the ambient run comment.
 */
export async function submitAnnotation(
  runId: string,
  item: Pick<GalleryItem, "taskId" | "requestId">,
  region: Region | null,
  text: string,
): Promise<SubmitResult> {
  const message = formatRegionText(region, text);
  if (!message) throw new Error("write a note first");
  const sent: SubmitResult["sent"] = [];
  if (item.taskId) {
    await postCardComment(runId, item.taskId, message);
    sent.push("card comment");
  }
  if (item.requestId) {
    await postVisualFeedback(runId, item.requestId, message);
    sent.push("visual feedback");
  }
  if (sent.length === 0) {
    await postRunComment(runId, message);
    sent.push("run comment");
  }
  return { sent };
}
