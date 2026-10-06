import { sparklinePoints, sparklineSummary } from "./model";

/**
 * A tiny inline-SVG line chart. Every point is a circle, and the accessible name is a text
 * summary of the series, so nothing depends on seeing the line.
 */
export function Sparkline({ label, values, width = 120, height = 32 }: {
  label: string;
  values: readonly number[];
  width?: number;
  height?: number;
}) {
  const points = sparklinePoints(values, width, height);
  const summary = sparklineSummary(label, values);
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      role="img"
      aria-label={summary}
      className="text-primary"
      data-testid="sparkline"
    >
      <title>{summary}</title>
      {points.length > 1 && (
        <polyline
          points={points.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ")}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
      )}
      {points.map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r={i === points.length - 1 ? 3 : 2} fill="currentColor" data-testid="spark-point" />
      ))}
    </svg>
  );
}
