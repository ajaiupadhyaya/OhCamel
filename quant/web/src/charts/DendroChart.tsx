/**
 * A clustering tree in SVG from scipy's plot coordinates (charts/dendro). Links inside a
 * cluster (below the cut) are ink, links above it ink-2; the cut is a dotted rule labelled
 * with the cluster count. Leaves carry the ticker and, when given, its weight. The readout
 * strip names the merge under the pointer: members, merge distance, combined weight.
 *
 *   <DendroChart d={result.extra.dendrogram} weights={result.weights} />
 */
import { useMemo, useState } from "react";
import { fmtNum, fmtPct } from "../lib/format";
import { niceTicks } from "./scales";
import { dendroModel, type DendroInput } from "./dendro";
import { useChartTheme } from "./theme";
import { useWidth } from "./useWidth";

const STRIP = 20;

export function DendroChart({ d, weights, height = 300, ariaLabel = "Clustering tree" }: { d: DendroInput; weights?: Record<string, number>; height?: number; ariaLabel?: string }) {
  const t = useChartTheme();
  const [box, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const m = useMemo(() => dendroModel(d), [d]);
  const plotH = height - STRIP;
  const left = 40;
  const n = d.ivl.length;
  // too many leaves for the width: tickers turn vertical and the weights go to the readout
  const tight = (width - left - 6) / Math.max(1, n) < 28;
  const longest = Math.max(1, ...d.ivl.map((k) => k.length));
  const bottom = tight ? Math.ceil(longest * 6) + 10 : weights ? 34 : 20;
  const ticks = niceTicks(0, m.maxH, 4);
  const top = ticks[ticks.length - 1] || 1;
  const W = Math.max(10, width - left - 6);
  const H = plotH - 8 - bottom;
  const X = (v: number) => left + (v / (10 * n)) * W;
  const Y = (v: number) => 8 + H - (v / top) * H;
  const font = (px: number) => ({ fontSize: px, fontFamily: t.mono });
  const leafFont = tight ? 9 : 11;

  const root = m.segs.reduce((b, s, i) => (s.top > (m.segs[b]?.top ?? -1) ? i : b), 0);
  const shown = hover ?? root;
  const s = m.segs[shown];
  const mem = s ? m.members(s) : [];
  const w = weights && s ? mem.reduce((a, k) => a + (weights[k] ?? 0), 0) : null;

  return (
    <div ref={box} className="oc-chart oc-chart-dendro">
      <div className="oc-chart-readout num" aria-live="off">
        {s && (
          <>
            <span className="oc-chart-readout-k">{mem.length > 6 ? `${mem.slice(0, 6).join(" ")} +${mem.length - 6}` : mem.join(" ")}</span> · <span className="oc-chart-readout-k">DIST</span>{" "}
            <span className="oc-chart-readout-v">{fmtNum(s.top, 3)}</span>
            {w !== null && (
              <>
                {" · "}
                <span className="oc-chart-readout-k">W</span> <span className="oc-chart-readout-v">{fmtPct(w, 1)}</span>
              </>
            )}
          </>
        )}
      </div>
      {width > 0 && (
        <svg width={width} height={plotH} viewBox={`0 0 ${width} ${plotH}`} role="img" aria-label={ariaLabel} onMouseLeave={() => setHover(null)}>
          {ticks.map((v) => (
            <g key={v}>
              <line x1={left} x2={left + W} y1={Math.round(Y(v)) + 0.5} y2={Math.round(Y(v)) + 0.5} stroke={v === 0 ? t.ink : t.ink3} strokeDasharray={v === 0 ? undefined : "1 3"} />
              <text x={left - 6} y={Y(v)} textAnchor="end" dominantBaseline="middle" fill={t.ink2} {...font(11)}>
                {fmtNum(v, 2)}
              </text>
            </g>
          ))}
          <line x1={left} x2={left + W} y1={Math.round(Y(m.cut)) + 0.5} y2={Math.round(Y(m.cut)) + 0.5} stroke={t.ink2} strokeDasharray="4 3" />
          <text x={left + W} y={Y(m.cut) - 4} textAnchor="end" fill={t.ink2} {...font(10)}>
            CUT · {m.nClusters} CLUSTER{m.nClusters === 1 ? "" : "S"}
          </text>
          {m.segs.map((g, i) => {
            const inside = m.clusterOf.has(g);
            return (
              <path
                key={i}
                d={`M${X(g.ic[0])},${Y(g.dc[0])}V${Y(g.dc[1])}H${X(g.ic[3])}V${Y(g.dc[3])}`}
                fill="none"
                stroke={inside ? t.ink : t.ink2}
                strokeWidth={i === hover ? 2.5 : inside ? 1.5 : 1}
              />
            );
          })}
          {m.segs.map((g, i) => (
            <rect key={`n${i}`} x={X(g.mid) - 5} y={Y(g.top) - 5} width={10} height={10} fill="transparent" onMouseEnter={() => setHover(i)}>
              <title>{m.members(g).join(" ")}</title>
            </rect>
          ))}
          {m.segs.map((g, i) => (
            <rect key={`d${i}`} x={X(g.mid) - 2} y={Y(g.top) - 2} width={4} height={4} fill={t.ink} pointerEvents="none" />
          ))}
          {d.ivl.map((k, i) => (
            <g key={k}>
              {tight ? (
                <text x={X(m.leafX[i])} y={8 + H + 6} textAnchor="end" dominantBaseline="middle" transform={`rotate(-90 ${X(m.leafX[i])} ${8 + H + 6})`} fill={t.ink} {...font(leafFont)}>
                  {k}
                </text>
              ) : (
                <text x={X(m.leafX[i])} y={8 + H + 13} textAnchor="middle" fill={t.ink} {...font(leafFont)}>
                  {k}
                </text>
              )}
              {weights && !tight && (
                <text x={X(m.leafX[i])} y={8 + H + 27} textAnchor="middle" fill={t.ink2} {...font(leafFont - 1)}>
                  {fmtPct(weights[k] ?? 0, 1)}
                </text>
              )}
            </g>
          ))}
        </svg>
      )}
    </div>
  );
}
