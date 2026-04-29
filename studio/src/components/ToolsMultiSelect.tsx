import { useEffect, useRef, useState } from "react";

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
    <div ref={containerRef} className="tools-multiselect">
      {/* Selected chips */}
      <div className="chip-row" style={{ flexWrap: "wrap", gap: 4, marginBottom: 4 }}>
        {selected.map((s) => (
          <span key={s} className="tag small">
            {s}
            <button
              className="chip-remove"
              onClick={() => remove(s)}
              aria-label={`Remove ${s}`}
            >
              ×
            </button>
          </span>
        ))}
        <input
          className="chip-input"
          placeholder={selected.length ? "" : "Add tools…"}
          value={search}
          onFocus={() => setOpen(true)}
          onChange={(e) => { setSearch(e.target.value); setOpen(true); }}
          style={{ border: "none", outline: "none", flex: 1, minWidth: 80 }}
        />
      </div>

      {/* Dropdown list */}
      {open && filtered.length > 0 && (
        <div className="tools-dropdown" style={{
          position: "absolute",
          background: "var(--bg, #fff)",
          border: "1px solid var(--border, #ddd)",
          borderRadius: 6,
          zIndex: 100,
          maxHeight: 260,
          overflowY: "auto",
          width: "100%",
          boxShadow: "0 2px 8px rgba(0,0,0,.15)",
        }}>
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
                style={{
                  padding: "6px 10px",
                  cursor: enabled ? "pointer" : "not-allowed",
                  opacity: enabled ? 1 : 0.45,
                  background: isSelected ? "var(--accent-faint, #eef)" : "transparent",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <input
                  type="checkbox"
                  checked={isSelected}
                  disabled={!enabled}
                  readOnly
                  style={{ width: "auto", pointerEvents: "none" }}
                />
                <span style={{ fontFamily: "monospace", fontSize: 13 }}>{t.name}</span>
                {t.baseline && (
                  <span className="tag small" style={{ fontSize: 10 }}>baseline</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
