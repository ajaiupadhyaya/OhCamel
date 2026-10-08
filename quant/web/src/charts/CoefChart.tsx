/**
 * A coefficient plot in SVG: one row per term, the estimate as a square, its confidence
 * interval as a whisker, a solid ink zero rule. Significant terms are filled ink, the rest
 * open; the t-stat sits at the right edge in mono.
 *
 *   <CoefChart rows={[{ term: "MKT", est: 0.92, lo: 0.88, hi: 0.96, t: 41.2 }]} />
 * `tick` formats the axis and `right` the right-edge figure (e.g. percent estimates, a p-value).
 * The right-edge column is sized from the longest figure (measured in the mono face, never
 * narrower than the Plex Mono estimate); when that column would squeeze the plot
 * below a readable width, each figure drops to its own line under its whisker instead.
 */
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { fmtNum } from "../lib/format";
import { niceTicks } from "./scales";
import { useChartTheme } from "./theme";

export interface CoefRow {
  term: string;
  est: number;
  lo: number;
  hi: number;
  t: number;
}

const ROW = 26;
const ROW_STACKED = 40;
/** Advance of one IBM Plex Mono glyph at 11px (0.6em). */
export const MONO_ADVANCE = 6.6;
const GAP = 12;
const MIN_PLOT = 120;

export interface CoefLayout {
  labelW: number;
  plotL: number;
  plotR: number;
  /** Right-edge figures sit on their own line under each whisker. */
  stacked: boolean;
  row: number;
}

/** Rendered width of a right-edge figure: measured when possible, else the Plex Mono estimate. */
export type MeasureText = (s: string) => number;
const estimate: MeasureText = (s) => s.length * MONO_ADVANCE;

/** A canvas measure in the chart's own mono font, so a fallback face still clears the plot. */
function canvasMeasure(fontFamily: string): MeasureText {
  const ctx = typeof document === "undefined" ? null : document.createElement("canvas").getContext?.("2d");
  if (!ctx || !fontFamily) return estimate;
  ctx.font = `11px ${fontFamily}`;
  return (s) => ctx.measureText(s).width;
}

/** Horizontal layout: term column, plot, and a right column wide enough for the longest figure. */
export function coefLayout(rows: { term: string }[], rights: string[], width: number, measure: MeasureText = estimate): CoefLayout {
  const labelW = Math.min(110, Math.ceil(Math.max(3, ...rows.map((r) => r.term.length)) * 7) + 10);
  const widthOf = (s: string) => Math.max(measure(s) || 0, estimate(s));
  const tW = Math.ceil(Math.max(0, ...rights.map(widthOf))) + GAP;
  if (width - labelW - tW >= MIN_PLOT) return { labelW, plotL: labelW, plotR: width - tW, stacked: false, row: ROW };
  return { labelW, plotL: labelW, plotR: Math.max(labelW + 40, width - 20), stacked: true, row: ROW_STACKED };
}

export function CoefChart({
  rows,
  sigT = 2,
  ariaLabel,
  tick = (v) => fmtNum(v, 2),
  right = (r) => `t ${fmtNum(r.t, 1)}`,
}: {
  rows: CoefRow[];
  sigT?: number;
  ariaLabel: string;
  /** Axis tick text (default 2-decimal numbers). */
  tick?: (v: number) => string;
  /** The right-edge figure per row (default the t-stat). */
  right?: (r: CoefRow) => string;
}) {
  const t = useChartTheme();
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const node = box.current;
    if (!node) return;
    setWidth(node.clientWidth);
    const ro = new ResizeObserver((e) => setWidth(Math.round(e[0]?.contentRect.width ?? 0)));
    ro.observe(node);
    return () => ro.disconnect();
  }, []);
  const vals = rows.flatMap((r) => [r.lo, r.hi, 0]).filter(Number.isFinite);
  const ticks = niceTicks(Math.min(...vals), Math.max(...vals), 4);
  const lo = ticks[0] ?? -1;
  const hi = ticks[ticks.length - 1] ?? 1;
  const rights = rows.map(right);
  const measure = useMemo(() => canvasMeasure(t.mono), [t.mono]);
  const { plotL, plotR, stacked, row: rowH } = coefLayout(rows, rights, width, measure);
  const X = (v: number) => plotL + ((v - lo) / (hi - lo || 1)) * (plotR - plotL);
  const height = rows.length * rowH + 22;
  const font = (px: number, fam: string) => ({ fontSize: px, fontFamily: fam });
  return (
    <div ref={box} className="oc-chart oc-chart-coef">
      {width > 0 && (
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
          {ticks.map((v) => (
            <g key={v}>
              <line x1={X(v)} x2={X(v)} y1={0} y2={rows.length * rowH} stroke={t.ink3} strokeDasharray="1 3" />
              <text x={X(v)} y={rows.length * rowH + 15} textAnchor="middle" fill={t.ink2} {...font(11, t.mono)}>
                {tick(v)}
              </text>
            </g>
          ))}
          <line x1={X(0) + 0.5} x2={X(0) + 0.5} y1={0} y2={rows.length * rowH} stroke={t.ink} />
          {rows.map((r, i) => {
            const cy = stacked ? i * rowH + 13 : i * rowH + rowH / 2;
            const sig = Math.abs(r.t) >= sigT;
            return (
              <g key={r.term}>
                <text x={0} y={cy} dominantBaseline="middle" fill={t.ink} {...font(12, t.mono)}>
                  {r.term}
                </text>
                <line x1={X(r.lo)} x2={X(r.hi)} y1={cy} y2={cy} stroke={t.ink} />
                <line x1={X(r.lo)} x2={X(r.lo)} y1={cy - 4} y2={cy + 4} stroke={t.ink} />
                <line x1={X(r.hi)} x2={X(r.hi)} y1={cy - 4} y2={cy + 4} stroke={t.ink} />
                <rect x={X(r.est) - 4} y={cy - 4} width={8} height={8} fill={sig ? t.ink : t.paper} stroke={t.ink} />
                {stacked && <rect x={width - Math.max(measure(rights[i]), estimate(rights[i])) - 6} y={cy + 8} width={Math.max(measure(rights[i]), estimate(rights[i])) + 6} height={14} fill={t.paper} />}
                <text x={width} y={stacked ? cy + 15 : cy} textAnchor="end" dominantBaseline="middle" fill={sig ? t.ink : t.ink2} {...font(11, t.mono)}>
                  {rights[i]}
                </text>
                <line x1={0} x2={width} y1={(i + 1) * rowH - 0.5} y2={(i + 1) * rowH - 0.5} stroke={t.ink3} strokeDasharray="1 3" />
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}
