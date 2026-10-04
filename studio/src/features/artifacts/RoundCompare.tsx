import { useEffect, useState } from "react";
import { basename, type GalleryItem } from "./model";

interface Option {
  key: string;
  label: string;
  url: string;
}

/** True once `url` has loaded as an image (used to find optional review files). */
function useImageExists(url: string): boolean {
  const [exists, setExists] = useState(false);
  useEffect(() => {
    setExists(false);
    const probe = new Image();
    probe.onload = () => setExists(true);
    probe.src = url;
    return () => {
      probe.onload = null;
    };
  }, [url]);
  return exists;
}

/** "Round 2 · 14:03:11 · name.png" */
function roundLabel(item: GalleryItem, index: number): string {
  const time = item.ts ? new Date(item.ts).toLocaleTimeString() : "";
  return `Round ${index + 1}${time ? ` · ${time}` : ""} · ${basename(item.path)}`;
}

/**
 * Before/after slider across a card's review rounds.
 *
 * `rounds` are the card's images oldest first (see `orderRounds`). The two selects default to
 * the last two rounds. When the visual pre-check left a diff image or a baseline for the
 * card, they show up as extra choices and below the slider.
 */
export function RoundCompare({
  rounds,
  diffUrl,
  baselineUrl,
}: {
  rounds: readonly GalleryItem[];
  diffUrl: string;
  baselineUrl: string;
}) {
  const hasDiff = useImageExists(diffUrl);
  const hasBaseline = useImageExists(baselineUrl);

  const options: Option[] = [
    ...(hasBaseline ? [{ key: "baseline", label: "Baseline (last passed)", url: baselineUrl }] : []),
    ...rounds.map((r, i) => ({ key: r.id, label: roundLabel(r, i), url: r.url })),
  ];
  const [beforeKey, setBeforeKey] = useState<string | null>(null);
  const [afterKey, setAfterKey] = useState<string | null>(null);
  const [position, setPosition] = useState(50);

  const last = options.length - 1;
  const after = options.find((o) => o.key === afterKey) ?? options[last];
  const before = options.find((o) => o.key === beforeKey) ?? options[Math.max(last - 1, 0)];

  if (!before || !after) {
    return <p className="m-0 text-sm text-muted-foreground">Nothing to compare yet.</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3 text-sm">
        <label className="flex flex-col gap-1">
          Before
          <select
            value={before.key}
            onChange={(e) => setBeforeKey(e.target.value)}
            className="rounded-md border border-input bg-background p-1 text-foreground"
          >
            {options.map((o) => (
              <option key={o.key} value={o.key}>{o.label}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          After
          <select
            value={after.key}
            onChange={(e) => setAfterKey(e.target.value)}
            className="rounded-md border border-input bg-background p-1 text-foreground"
          >
            {options.map((o) => (
              <option key={o.key} value={o.key}>{o.label}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="relative w-full overflow-hidden rounded-md border border-border bg-muted" data-testid="compare-stage">
        <img src={after.url} alt="After" loading="lazy" className="block w-full" data-testid="compare-after" />
        <div
          className="absolute inset-0 overflow-hidden"
          // The slider position is a runtime value, so the clip cannot be a utility class.
          style={{ clipPath: `inset(0 ${100 - position}% 0 0)` }}
        >
          <img src={before.url} alt="Before" loading="lazy" className="h-full w-full object-contain" data-testid="compare-before" />
        </div>
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 w-0.5 bg-primary"
          // Same runtime position as the clip above.
          style={{ left: `${position}%` }}
        />
      </div>
      <input
        type="range"
        min={0}
        max={100}
        value={position}
        aria-label="Before and after position"
        onChange={(e) => setPosition(Number(e.target.value))}
        className="w-full accent-primary"
      />

      {hasDiff && (
        <figure className="m-0 flex flex-col gap-1">
          <figcaption className="text-sm font-medium">Visual pre-check diff</figcaption>
          <img src={diffUrl} alt="Pixel diff from the visual pre-check" loading="lazy" className="max-h-[40vh] self-start rounded-md border border-border" />
        </figure>
      )}
    </div>
  );
}
