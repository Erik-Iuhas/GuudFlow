import {
  TOGGLE_ORDER,
  VISION_DISABLED_TITLE,
  type CapabilityBlock,
} from "../lib/capabilities";

interface CapabilitySwitchesProps {
  /** Role name, used for accessible labels. */
  role: string;
  /** Resolved role type (builder, reviewer, ...). */
  roleType: string;
  /** Current effective capabilities for this role. */
  value: CapabilityBlock;
  onChange: (next: CapabilityBlock) => void;
  /** Allowed values per toggle, from GET /api/harness/capabilities. */
  levels: Record<string, string[]>;
  /** Defaults for this role type; "reset to defaults" restores them. */
  defaults?: CapabilityBlock;
  /** False when the role's model has no image input. */
  visionAvailable?: boolean;
  /** True when GUUDFLOW_ENV=production: host_dev cannot be chosen. */
  production?: boolean;
  hostDevWarning?: string;
  /** Validation message from the resolve endpoint, if any. */
  error?: string | null;
  disabled?: boolean;
}

/**
 * The seven capability toggles for one role (plan §5.7), as a compact grid of
 * selects. Controlled: the parent owns `value` and persists it.
 */
export function CapabilitySwitches({
  role,
  roleType,
  value,
  onChange,
  levels,
  defaults,
  visionAvailable = true,
  production = false,
  hostDevWarning = "",
  error = null,
  disabled = false,
}: CapabilitySwitchesProps) {
  const toggles = TOGGLE_ORDER.filter((name) => levels[name]);

  function set(name: string, level: string) {
    onChange({ ...value, [name]: level });
  }

  return (
    <div className="capability-switches" data-testid={`capabilities-${role}`}>
      <div
        className="row"
        style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}
      >
        <span className="small">
          Capabilities <span className="tag mono">{roleType}</span>
        </span>
        {defaults && (
          <button
            type="button"
            className="link small"
            disabled={disabled}
            onClick={() => onChange({ ...defaults })}
            style={{ background: "none", border: "none", padding: 0, color: "#2563eb", cursor: "pointer" }}
          >
            reset to defaults
          </button>
        )}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
          gap: 8,
        }}
      >
        {toggles.map((name) => {
          const visionOff = name === "vision" && !visionAvailable;
          const isDisabled = disabled || visionOff;
          return (
            <label
              key={name}
              className="small"
              title={visionOff ? VISION_DISABLED_TITLE : undefined}
              style={{ display: "flex", flexDirection: "column", gap: 2 }}
            >
              <span className="muted">{name}</span>
              <select
                aria-label={`${role} ${name}`}
                value={value[name] ?? levels[name][0]}
                disabled={isDisabled}
                title={visionOff ? VISION_DISABLED_TITLE : undefined}
                onChange={(e) => set(name, e.target.value)}
              >
                {levels[name].map((level) => {
                  const hostDev = name === "sandbox" && level === "host_dev";
                  const blocked = hostDev && production;
                  return (
                    <option
                      key={level}
                      value={level}
                      disabled={blocked}
                      title={hostDev ? hostDevWarning : undefined}
                    >
                      {blocked ? `${level} (not in production)` : level}
                    </option>
                  );
                })}
              </select>
            </label>
          );
        })}
      </div>

      {value.sandbox === "host_dev" && hostDevWarning && (
        <div className="warn small" role="alert" style={{ marginTop: 6 }}>
          {hostDevWarning}
        </div>
      )}
      {error && (
        <div className="error small" role="alert" style={{ marginTop: 6 }}>
          {error}
        </div>
      )}
    </div>
  );
}
