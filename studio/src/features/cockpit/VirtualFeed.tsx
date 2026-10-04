import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Button } from "../../components/ui/button";
import { cn } from "../../lib/utils";

/** How close to the bottom (px) still counts as "following the feed". */
const STICK_THRESHOLD_PX = 48;

export interface VirtualFeedProps<T> {
  items: readonly T[];
  getKey: (item: T, index: number) => string;
  renderRow: (item: T, index: number) => ReactNode;
  /** Accessible name of the scrolling log. */
  label: string;
  /** Rendered instead of the list when there are no items. */
  empty?: ReactNode;
  /** Row height guess before a row is measured. */
  estimateSize?: number;
  overscan?: number;
  className?: string;
  testId?: string;
}

/**
 * A windowed, auto-scrolling list (TanStack Virtual). It follows the newest row while the
 * reader is at the bottom; once they scroll up it stays put and offers "Jump to latest".
 * Rows are measured, so variable-height rows (expanding tool calls) work.
 */
export function VirtualFeed<T>({
  items, getKey, renderRow, label, empty, estimateSize = 56, overscan = 8, className, testId,
}: VirtualFeedProps<T>) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  const followingRef = useRef(true);

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => estimateSize,
    overscan,
    getItemKey: (i) => getKey(items[i], i),
  });

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_THRESHOLD_PX;
    followingRef.current = atBottom;
    setFollowing((prev) => (prev === atBottom ? prev : atBottom));
  }, []);

  // Keep the newest row in view while following.
  useLayoutEffect(() => {
    if (followingRef.current && items.length > 0) {
      virtualizer.scrollToIndex(items.length - 1, { align: "end" });
    }
  }, [items.length, virtualizer]);

  // Rows grow when expanded or measured late; keep following through that too.
  const total = virtualizer.getTotalSize();
  useEffect(() => {
    const el = scrollRef.current;
    if (el && followingRef.current && items.length > 0) el.scrollTop = el.scrollHeight;
  }, [total, items.length]);

  const jumpToLatest = useCallback(() => {
    followingRef.current = true;
    setFollowing(true);
    if (items.length > 0) virtualizer.scrollToIndex(items.length - 1, { align: "end" });
  }, [items.length, virtualizer]);

  if (items.length === 0) {
    return <div className={cn("p-3 text-sm text-muted-foreground", className)}>{empty}</div>;
  }

  return (
    <div className={cn("relative min-h-0", className)}>
      <div
        ref={scrollRef}
        role="log"
        aria-label={label}
        data-testid={testId}
        tabIndex={0}
        onScroll={onScroll}
        className="h-full overflow-y-auto rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {/* TanStack Virtual positions rows with a total height and per-row offsets. These two
            style objects are the library's required inline positioning, not decoration. */}
        <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
          {virtualizer.getVirtualItems().map((row) => (
            <div
              key={row.key}
              data-index={row.index}
              ref={virtualizer.measureElement}
              className="absolute left-0 top-0 w-full"
              style={{ transform: `translateY(${row.start}px)` }}
            >
              {renderRow(items[row.index], row.index)}
            </div>
          ))}
        </div>
      </div>
      {!following && (
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className="absolute bottom-2 right-4 shadow-md"
          onClick={jumpToLatest}
        >
          Jump to latest
        </Button>
      )}
    </div>
  );
}
