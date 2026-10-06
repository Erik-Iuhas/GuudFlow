import type { ReactNode } from "react";
import type { RawSignal } from "../../../lib/runEvents.gen";
import { createRunStore, type RunStore } from "../../../stores/runEvents";
import { RunStoreProvider } from "../../../stores/runStoreContext";

export interface ArtifactSpec {
  id: string;
  path: string;
  kind?: string;
  role?: string;
  task?: string | null;
  size?: number;
  at?: string;
}

export function artifactSignal(spec: ArtifactSpec): RawSignal {
  const role = spec.role ?? "frontend";
  const detail = {
    path: spec.path,
    kind: spec.kind ?? "image",
    role,
    task_id: spec.task ?? null,
    size: spec.size ?? 2048,
    sha256: "0".repeat(64),
    created_at: spec.at ?? "2026-05-01T10:00:00+00:00",
    served_path: `artifacts/${role}/${spec.path}`,
  };
  return {
    id: spec.id, type: "ARTIFACT", summary: `${role}: ${spec.path}`, detail: JSON.stringify(detail),
    from_agent: role, priority: "normal", iteration: 0, timestamp: detail.created_at,
  };
}

export function screenshotSignal(
  id: string, role: string, path: string, extra: Record<string, unknown> = {}, at = "2026-05-01T10:00:00+00:00",
): RawSignal {
  return {
    id, type: "SCREENSHOT_READY", summary: "Screenshot", detail: JSON.stringify({ role, path, ...extra }),
    from_agent: role, priority: "normal", iteration: 0, timestamp: at,
  };
}

export function makeStore(signals: RawSignal[], runId = "run-1"): RunStore {
  const store = createRunStore(runId);
  store.getState().ingest(signals);
  return store;
}

export function withStore(store: RunStore, children: ReactNode) {
  return <RunStoreProvider store={store}>{children}</RunStoreProvider>;
}
