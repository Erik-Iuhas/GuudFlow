import { useEffect, useState } from "react";
import { getToken } from "../api";

export interface GraphNode {
  role: string;
  archetype: string;
  persona_name: string;
  persona_id: string | null;
  model_tier: string;
  is_final_reviewer: boolean;
}

export interface GraphEdge {
  from_role: string;
  to_role: string;
  kind: "depends_on" | "can_assign_to" | "escalates_to";
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export function useRunGraph(runId: string | null): {
  data: GraphData | null;
  loading: boolean;
  error: string | null;
} {
  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    setError(null);
    const token = getToken();
    fetch(`/api/runs/${encodeURIComponent(runId)}/graph`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json() as Promise<GraphData>;
      })
      .then((d) => setData(d))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [runId]);

  return { data, loading, error };
}
