/**
 * A coefficient plot in SVG: one row per term, the estimate as a square, its confidence
 * interval as a whisker, a solid ink zero rule. Significant terms are filled ink, the rest
 * open; the t-stat sits at the right edge in mono.
 *
 *   <CoefChart rows={[{ term: "MKT", est: 0.92, lo: 0.88, hi: 0.96, t: 41.2 }]} />
 */
import { useLayoutEffect, useRef, useState } from "react";
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

export function CoefChart({ rows, sigT = 2, ariaLabel }: { rows: CoefRow[]; sigT?: number; ariaLabel: string }) {
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
  const labelW = Math.min(110, Math.ceil(Math.max(3, ...rows.map((r) => r.term.length)) * 7) + 10);
  const tW = 64;
  const plotL = labelW;
  const plotR = Math.max(plotL + 40, width - tW);
  const X = (v: number) => plotL + ((v - lo) / (hi - lo || 1)) * (plotR - plotL);
  const height = rows.length * ROW + 22;
  const font = (px: number, fam: string) => ({ fontSize: px, fontFamily: fam });
  return (
    <div ref={box} className="oc-chart oc-chart-coef">
      {width > 0 && (
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
          {ticks.map((v) => (
            <g key={v}>
              <line x1={X(v)} x2={X(v)} y1={0} y2={rows.length * ROW} stroke={t.ink3} strokeDasharray="1 3" />
              <text x={X(v)} y={rows.length * ROW + 15} textAnchor="middle" fill={t.ink2} {...font(11, t.mono)}>
                {fmtNum(v, 2)}
              </text>
            </g>
          ))}
          <line x1={X(0) + 0.5} x2={X(0) + 0.5} y1={0} y2={rows.length * ROW} stroke={t.ink} />
          {rows.map((r, i) => {
            const cy = i * ROW + ROW / 2;
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
                <text x={width} y={cy} textAnchor="end" dominantBaseline="middle" fill={sig ? t.ink : t.ink2} {...font(11, t.mono)}>
                  t {fmtNum(r.t, 1)}
                </text>
                <line x1={0} x2={width} y1={(i + 1) * ROW - 0.5} y2={(i + 1) * ROW - 0.5} stroke={t.ink3} strokeDasharray="1 3" />
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}
