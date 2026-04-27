import type { AgentConfig } from "../types";

interface Props {
  agent: AgentConfig;
  allRoles: string[];
  onChange: (partial: Partial<AgentConfig>) => void;
  onRemove: () => void;
}

interface ModelOption {
  value: string;       // stored as model_tier (tier name or model ID)
  label: string;
  sublabel: string;
  provider: string;
}

const ANTHROPIC_MODELS: ModelOption[] = [
  { value: "heavy",   label: "Heavy",  sublabel: "claude-opus-4-7",            provider: "Anthropic" },
  { value: "solid",   label: "Solid",  sublabel: "claude-sonnet-4-6",          provider: "Anthropic" },
  { value: "fast",    label: "Fast",   sublabel: "claude-haiku-4-5-20251001",  provider: "Anthropic" },
];

export function AgentCard({ agent, allRoles, onChange, onRemove }: Props) {
  return (
    <div className="card agent-card">
      <button className="remove" onClick={onRemove} title="remove">×</button>
      <div className="role">{agent.role}</div>

      <label className="small">Description</label>
      <input
        value={agent.description ?? ""}
        onChange={(e) => onChange({ description: e.target.value })}
      />

      <label className="small" style={{ marginTop: 8 }}>System prompt</label>
      <textarea
        rows={3}
        value={agent.system_prompt ?? ""}
        onChange={(e) => onChange({ system_prompt: e.target.value })}
      />

      <div style={{ marginTop: 8 }}>
        <label className="small">Model</label>
        <select
          value={agent.model_tier ?? "solid"}
          onChange={(e) => onChange({ model_tier: e.target.value as AgentConfig["model_tier"] })}
          style={{ width: "100%" }}
        >
          {ANTHROPIC_MODELS.map((m) => (
            <option key={m.value} value={m.value}>
              {m.provider} — {m.label} ({m.sublabel})
            </option>
          ))}
        </select>
      </div>

      <label className="small">Tools (comma-separated)</label>
      <input
        value={(agent.declared_local_tools ?? []).join(", ")}
        onChange={(e) =>
          onChange({
            declared_local_tools: e.target.value
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean),
          })
        }
      />

      <label className="small" style={{ marginTop: 8 }}>Depends on</label>
      <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
        {allRoles
          .filter((r) => r !== agent.role)
          .map((r) => {
            const checked = (agent.depends_on ?? []).includes(r);
            return (
              <label key={r} className="small" style={{ marginBottom: 0 }}>
                <input
                  type="checkbox"
                  checked={checked}
                  style={{ width: "auto", marginRight: 4 }}
                  onChange={() => {
                    const next = checked
                      ? (agent.depends_on ?? []).filter((x) => x !== r)
                      : [...(agent.depends_on ?? []), r];
                    onChange({ depends_on: next });
                  }}
                />
                {r}
              </label>
            );
          })}
      </div>

      <div className="row" style={{ marginTop: 8 }}>
        <label className="small" style={{ margin: 0 }}>
          <input
            type="checkbox"
            checked={agent.is_reviewer ?? false}
            style={{ width: "auto", marginRight: 4 }}
            onChange={(e) => onChange({ is_reviewer: e.target.checked })}
          />
          Reviewer (merges branches on APPROVED)
        </label>
      </div>

      <div className="row" style={{ marginTop: 8, gap: 12 }}>
        <div style={{ flex: 1 }}>
          <label className="small">Max tool iterations</label>
          <input
            type="number"
            min={1}
            max={50}
            value={agent.max_tool_iterations ?? 6}
            onChange={(e) =>
              onChange({
                max_tool_iterations: parseInt(e.target.value || "6", 10),
              })
            }
          />
        </div>
      </div>
    </div>
  );
}
