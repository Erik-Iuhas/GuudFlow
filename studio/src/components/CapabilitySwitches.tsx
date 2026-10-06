import {
  TOGGLE_ORDER,
  TOGGLE_TOOLTIPS,
  VISION_DISABLED_TITLE,
  type CapabilityBlock,
} from "../lib/capabilities";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Switch } from "./ui/switch";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "./ui/tooltip";

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
 * The seven capability toggles for one role (plan §5.7), as a compact grid. Two-level
 * toggles whose first level is "off" render as switches; the rest stay selects.
 * Controlled: the parent owns `value` and persists it.
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
    <TooltipProvider delayDuration={150}>
      <div data-testid={`capabilities-${role}`}>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-xs">
            Capabilities <Badge variant="secondary" className="font-mono">{roleType}</Badge>
          </span>
          {defaults && (
            <Button
              type="button"
              variant="link"
              size="sm"
              className="h-auto p-0 text-xs"
              disabled={disabled}
              onClick={() => onChange({ ...defaults })}
            >
              reset to defaults
            </Button>
          )}
        </div>

        <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-2">
          {toggles.map((name) => {
            const visionOff = name === "vision" && !visionAvailable;
            const isDisabled = disabled || visionOff;
            const current = value[name] ?? levels[name][0];
            const binary = isBinary(levels[name]);
            return (
              <div key={name} className="flex flex-col gap-0.5 text-xs">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span
                      tabIndex={0}
                      className="w-fit cursor-help font-medium text-muted-foreground underline decoration-dotted underline-offset-2"
                      data-testid={`toggle-label-${role}-${name}`}
                    >
                      {name}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>{TOGGLE_TOOLTIPS[name]}</TooltipContent>
                </Tooltip>
                {binary ? (
                  <SwitchField
                    label={`${role} ${name}`}
                    levels={levels[name]}
                    current={current}
                    disabled={isDisabled}
                    hint={visionOff ? VISION_DISABLED_TITLE : undefined}
                    onChange={(level) => set(name, level)}
                  />
                ) : (
                  <select
                    aria-label={`${role} ${name}`}
                    value={current}
                    disabled={isDisabled}
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
                )}
              </div>
            );
          })}
        </div>

        {value.sandbox === "host_dev" && hostDevWarning && (
          <div
            role="alert"
            className="mt-1.5 rounded-md border border-status-blocked/40 bg-status-blocked/10 p-2 text-xs text-status-blocked"
          >
            {hostDevWarning}
          </div>
        )}
        {error && (
          <div
            role="alert"
            className="mt-1.5 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive"
          >
            {error}
          </div>
        )}
      </div>
    </TooltipProvider>
  );
}

/** A toggle with exactly two levels, the first being "off", is an on/off switch. */
function isBinary(levels: string[]): boolean {
  return levels.length === 2 && levels[0] === "off";
}

function SwitchField({
  label,
  levels,
  current,
  disabled,
  hint,
  onChange,
}: {
  label: string;
  levels: string[];
  current: string;
  disabled: boolean;
  /** Why the switch is unavailable; shown as a tooltip. */
  hint?: string;
  onChange: (level: string) => void;
}) {
  const [off, on] = levels;
  const field = (
    <span className="inline-flex items-center gap-2" tabIndex={hint ? 0 : undefined} data-testid={hint ? "switch-hint-trigger" : undefined}>
      <Switch
        aria-label={label}
        checked={current !== off}
        disabled={disabled}
        onCheckedChange={(checked) => onChange(checked ? on : off)}
      />
      <span className="font-mono">{current}</span>
    </span>
  );
  if (!hint) return field;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{field}</TooltipTrigger>
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  );
}
