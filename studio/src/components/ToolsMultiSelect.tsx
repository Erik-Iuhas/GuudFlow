import { useEffect, useRef, useState } from "react";
import { checkCls } from "../lib/field";
import { cn } from "../lib/utils";
import { Badge } from "./ui/badge";

export interface ToolEntry {
  name: string;
  scope: string;
  description: string;
  baseline: boolean;
  granted_by_archetypes?: string[];
}

interface Props {
  selected: string[];
  onChange: (next: string[]) => void;
  archetypeRoleType: string | null;
  projectRbacScopes: string[];
}

// Module-level cache so we fetch once per page load.
let _toolsPromise: Promise<ToolEntry[]> | null = null;

function fetchTools(): Promise<ToolEntry[]> {
  if (!_toolsPromise) {
    _toolsPromise = import("../api").then(({ toolsApi }) => toolsApi.list());
  }
  return _toolsPromise;
}

function isEnabled(
  tool: ToolEntry,
  archetypeRoleType: string | null,
  projectRbacScopes: string[],
): boolean {
  if (tool.baseline) return true;
  if (
    archetypeRoleType &&
    (tool.granted_by_archetypes ?? []).includes(archetypeRoleType)
  )
    return true;
  if (projectRbacScopes.includes(tool.scope)) return true;
  return false;
}

export function ToolsMultiSelect({
  selected,
  onChange,
  archetypeRoleType,
  projectRbacScopes,
}: Props) {
  const [tools, setTools] = useState<ToolEntry[]>([]);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchTools().then(setTools).catch(() => {});
  }, []);

  // Close on outside click
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const filtered = tools.filter((t) =>
    !search || t.name.toLowerCase().includes(search.toLowerCase()),
  );

  function toggle(scope: string) {
    if (selected.includes(scope)) {
      onChange(selected.filter((s) => s !== scope));
    } else {
      onChange([...selected, scope]);
    }
  }

  function remove(scope: string) {
    onChange(selected.filter((s) => s !== scope));
  }

  return (
    <div ref={containerRef} className="relative">
      {/* Selected chips */}
      <div className="mb-1 flex flex-wrap items-center gap-1">
        {selected.map((s) => (
          <Badge key={s} variant="secondary">
            {s}
            <button
              type="button"
              className="cursor-pointer border-0 bg-transparent p-0 text-muted-foreground hover:text-foreground"
              onClick={() => remove(s)}
              aria-label={`Remove ${s}`}
            >
              ×
            </button>
          </Badge>
        ))}
        <input
          className="min-w-20 flex-1 border-0 bg-transparent p-0 text-sm text-foreground outline-none"
          placeholder={selected.length ? "" : "Add tools…"}
          value={search}
          onFocus={() => setOpen(true)}
          onChange={(e) => { setSearch(e.target.value); setOpen(true); }}
        />
      </div>

      {/* Dropdown list */}
      {open && filtered.length > 0 && (
        <div className="absolute z-[100] max-h-[260px] w-full overflow-y-auto rounded-md border border-border bg-popover text-popover-foreground shadow-md">
          {filtered.map((t) => {
            const enabled = isEnabled(t, archetypeRoleType, projectRbacScopes);
            const isSelected = selected.includes(t.scope);
            const tooltip = enabled
              ? t.description
              : `Requires scope ${t.scope}. This role's archetype doesn't grant it; add to project RBAC to enable.`;
            return (
              <div
                key={t.name}
                title={tooltip}
                onClick={() => enabled && toggle(t.scope)}
                aria-disabled={!enabled}
                className={cn(
                  "flex items-center gap-2 px-2.5 py-1.5",
                  enabled ? "cursor-pointer" : "cursor-not-allowed opacity-45",
                  isSelected && "bg-accent",
                )}
              >
                <input
                  type="checkbox"
                  checked={isSelected}
                  disabled={!enabled}
                  readOnly
                  className={cn(checkCls, "pointer-events-none")}
                />
                <span className="font-mono text-[13px]">{t.name}</span>
                {t.baseline && (
                  <Badge variant="secondary" className="text-[10px]">baseline</Badge>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
