import { useCallback, useState } from "react";

/** The session page's node view: a card per role (Team, the default) or the node graph. */
export type NodeView = "team" | "graph";

export const NODE_VIEW_KEY = "guudflow.cockpit.view";

function read(): NodeView {
  try {
    return window.localStorage.getItem(NODE_VIEW_KEY) === "graph" ? "graph" : "team";
  } catch {
    return "team";
  }
}

/** The chosen node view, remembered per viewer in localStorage. */
export function useNodeView(): [NodeView, (view: NodeView) => void] {
  const [view, setView] = useState<NodeView>(read);
  const set = useCallback((next: NodeView) => {
    setView(next);
    try {
      window.localStorage.setItem(NODE_VIEW_KEY, next);
    } catch {
      /* storage blocked: the choice lasts for this page only */
    }
  }, []);
  return [view, set];
}
