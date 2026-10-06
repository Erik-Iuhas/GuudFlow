import { cn } from "../../lib/utils";

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
}

/** A small segmented switch: real buttons with `aria-pressed`, one pressed at a time. */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
  className,
}: {
  value: T;
  onChange: (value: T) => void;
  options: readonly SegmentedOption<T>[];
  /** Accessible name of the group. */
  label: string;
  className?: string;
}) {
  return (
    <div role="group" aria-label={label} className={cn("inline-flex items-center gap-0.5 rounded-md bg-muted p-0.5", className)}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(o.value)}
            className={cn(
              "cursor-pointer rounded-sm border border-transparent px-2.5 py-0.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              on
                ? "bg-background text-foreground shadow-sm hover:bg-background"
                : "bg-transparent text-muted-foreground hover:bg-accent hover:text-accent-foreground",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
