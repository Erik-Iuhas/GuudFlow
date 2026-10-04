import type { ArtifactView } from "../../stores/runEvents";

/** How the gallery renders a file. `screenshot` is a raster image taken by the host tool. */
export type ItemKind = "image" | "screenshot" | "svg" | "html" | "pdf" | "file";

/** One tile in the gallery: an ARTIFACT or SCREENSHOT_READY signal, resolved to a served URL. */
export interface GalleryItem {
  id: string;
  name: string;
  /** Path as the signal reported it (relative to the clone or the run folder). */
  path: string;
  kind: ItemKind;
  role: string | null;
  taskId: string | null;
  size: number | null;
  createdAt: string;
  /** `createdAt` as epoch ms (0 when unparsable). */
  ts: number;
  /** Arrival order, the tie-breaker for equal timestamps. */
  order: number;
  /** Read-only URL served by `GET /api/runs/{id}/artifact-files/...`. */
  url: string;
  requestId: string | null;
  question: string | null;
}

export interface GalleryFilters {
  /** A task id, `NO_TASK`, or null for all. */
  task: string | null;
  role: string | null;
}

/** Filter value for artifacts that no card owns. */
export const NO_TASK = "__none__";

/** Above this many items the grid renders only the rows in view. */
export const VIRTUALIZE_THRESHOLD = 200;

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "webp", "gif"]);

/** Served URL of a file under the run folder (`artifacts/…`, `screenshots/…`, `visual/…`). */
export function artifactUrl(runId: string, runRelativePath: string): string {
  const path = runRelativePath
    .split("/")
    .filter((part) => part !== "")
    .map(encodeURIComponent)
    .join("/");
  return `/api/runs/${encodeURIComponent(runId)}/artifact-files/${path}`;
}

export function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

function extension(path: string): string {
  const name = basename(path);
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

function kindOf(view: ArtifactView): ItemKind {
  const ext = extension(view.path);
  if (ext === "svg" || view.kind === "svg") return "svg";
  if (ext === "html" || ext === "htm" || view.kind === "html") return "html";
  if (ext === "pdf" || view.kind === "pdf") return "pdf";
  if (view.source === "screenshot") return "screenshot";
  if (IMAGE_EXTS.has(ext) || view.kind === "image") return "image";
  return "file";
}

/** Run-relative path the file is served from. */
function servedPathOf(view: ArtifactView): string {
  if (view.servedPath) return view.servedPath;
  if (view.path.startsWith("screenshots/")) return view.path;
  // A screenshot posted for visual feedback reports a clone path; the registry copies
  // changed files to artifacts/<role>/<path>.
  return `artifacts/${view.role ?? "unknown"}/${view.path.replace(/^\/+/, "")}`;
}

function isImageKind(kind: ItemKind): boolean {
  return kind === "image" || kind === "screenshot";
}

/**
 * Turn the store's `artifacts` slice into gallery items, newest first.
 *
 * The runner registers every host screenshot twice: as an ARTIFACT (with the card) and as
 * SCREENSHOT_READY (with the request id). Those pairs, matched on role and file name, are
 * merged into one tile that keeps both.
 */
export function buildItems(views: readonly ArtifactView[], runId: string): GalleryItem[] {
  const items: GalleryItem[] = [];
  const byRoleName = new Map<string, GalleryItem>();
  views.forEach((view, order) => {
    const kind = kindOf(view);
    const item: GalleryItem = {
      id: view.signalId,
      name: basename(view.path),
      path: view.path,
      kind,
      role: view.role,
      taskId: view.taskId,
      size: view.size,
      createdAt: view.createdAt,
      ts: Date.parse(view.createdAt) || 0,
      order,
      url: artifactUrl(runId, servedPathOf(view)),
      requestId: view.requestId ?? null,
      question: view.question ?? null,
    };
    const key = `${view.role ?? ""}\u0000${item.name}`;
    const twin = isImageKind(kind) ? byRoleName.get(key) : undefined;
    if (twin) {
      twin.kind = "screenshot";
      twin.taskId = twin.taskId ?? item.taskId;
      twin.size = twin.size ?? item.size;
      twin.requestId = twin.requestId ?? item.requestId;
      twin.question = twin.question ?? item.question;
      return;
    }
    if (isImageKind(kind)) byRoleName.set(key, item);
    items.push(item);
  });
  return items.sort((a, b) => b.ts - a.ts || b.order - a.order);
}

export function filterItems(items: readonly GalleryItem[], filters: GalleryFilters): GalleryItem[] {
  return items.filter((item) => {
    if (filters.role && item.role !== filters.role) return false;
    if (filters.task === NO_TASK) return item.taskId === null;
    if (filters.task && item.taskId !== filters.task) return false;
    return true;
  });
}

/** The image rounds of one card, oldest first (the order a before/after slider walks). */
export function orderRounds(items: readonly GalleryItem[], taskId: string): GalleryItem[] {
  return items
    .filter((item) => item.taskId === taskId && isImageKind(item.kind))
    .sort((a, b) => a.ts - b.ts || a.order - b.order);
}

export function isImageItem(item: GalleryItem): boolean {
  return isImageKind(item.kind);
}

/** "1.4 MB" style size; "unknown size" when the signal carried none. */
export function formatBytes(bytes: number | null): string {
  if (bytes === null) return "unknown size";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Served URLs of a card's visual pre-check diff image and last-passed baseline. */
export function visualReviewUrls(runId: string, taskId: string): { diff: string; baseline: string } {
  return {
    diff: artifactUrl(runId, `visual/${taskId}/diff_1.png`),
    baseline: artifactUrl(runId, `baselines/tasks/${taskId}.png`),
  };
}
