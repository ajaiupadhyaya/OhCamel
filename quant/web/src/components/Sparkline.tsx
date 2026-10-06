/**
 * Tiny inline SVG line (no Plotly) — for tables and tiles.
 *   <Sparkline values={closes} width={96} height={28} />
 * Colour follows the sign of last − first (gain/loss) unless `color` is given.
 * The area under the line is hatched with diagonal hairlines in the stroke colour,
 * never a gradient (spec 3.1/3.5).
 */
import { useId } from "react";

export function Sparkline({ values, width = 96, height = 28, color, area = true, strokeWidth = 1.5, baseline, className, title }: { values: (number | null | undefined)[]; width?: number; height?: number; color?: string; area?: boolean; strokeWidth?: number; baseline?: number; className?: string; title?: string }) {
  const id = `oc-spark-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const pts = values.map((v, i) => [i, v] as const).filter((p): p is readonly [number, number] => typeof p[1] === "number" && Number.isFinite(p[1]));
  if (pts.length < 2) return <svg width={width} height={height} className={className} aria-hidden />;
  const ys = pts.map((p) => p[1]);
  let lo = Math.min(...ys);
  let hi = Math.max(...ys);
  if (baseline !== undefined) {
    lo = Math.min(lo, baseline);
    hi = Math.max(hi, baseline);
  }
  const pad = strokeWidth + 1;
  const n = values.length - 1 || 1;
  const X = (i: number) => pad + (i / n) * (width - 2 * pad);
  const Y = (v: number) => pad + (1 - (v - lo) / (hi - lo || 1)) * (height - 2 * pad);
  const d = pts.map((p, k) => `${k ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
  const up = ys[ys.length - 1] >= ys[0];
  const stroke = color ?? (up ? "var(--gain)" : "var(--loss)");
  const last = pts[pts.length - 1];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className={`oc-sparkline ${className ?? ""}`} role={title ? "img" : undefined} aria-label={title} aria-hidden={title ? undefined : true}>
      {area && (
        <>
          <defs>
            <pattern id={id} patternUnits="userSpaceOnUse" width={4} height={4} patternTransform="rotate(45)">
              <line x1={0} y1={0} x2={0} y2={4} stroke={stroke} strokeWidth={1} strokeOpacity={0.4} />
            </pattern>
          </defs>
          <path d={`${d}L${X(last[0]).toFixed(1)},${height}L${X(pts[0][0]).toFixed(1)},${height}Z`} fill={`url(#${id})`} />
        </>
      )}
      {baseline !== undefined && <line x1={pad} x2={width - pad} y1={Y(baseline)} y2={Y(baseline)} stroke="var(--rule-strong)" strokeDasharray="2 2" strokeWidth={1} />}
      <path d={d} fill="none" stroke={stroke} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={X(last[0])} cy={Y(last[1])} r={2} fill={stroke} />
    </svg>
  );
}
