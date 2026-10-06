import { useCallback, useState } from "react";

/** The header's Overview / Technical switch; later packages read it to pick how much to show. */
export type DetailLevel = "overview" | "technical";

export const detailKey = (projectId: string) => `guudflow.detail.${projectId}`;

function read(projectId: string): DetailLevel {
  try {
    return window.localStorage.getItem(detailKey(projectId)) === "technical" ? "technical" : "overview";
  } catch {
    return "overview";
  }
}

/** The detail level of one project, kept in localStorage (a per-viewer convenience). */
export function useDetailLevel(projectId: string): [DetailLevel, (level: DetailLevel) => void] {
  // Re-read when the project changes, without an effect round trip.
  const [state, setState] = useState<{ id: string; level: DetailLevel }>(() => ({
    id: projectId,
    level: read(projectId),
  }));
  const level = state.id === projectId ? state.level : read(projectId);
  const set = useCallback(
    (next: DetailLevel) => {
      setState({ id: projectId, level: next });
      try {
        window.localStorage.setItem(detailKey(projectId), next);
      } catch {
        /* private window or blocked storage: the choice just lasts for this page */
      }
    },
    [projectId],
  );
  return [level, set];
}
