/**
 * A risk/return map in SVG: curves (an efficient frontier, a capital market line) and labelled
 * marks on two numeric axes, with direct mono labels placed clear of each other (charts/map)
 * and a readout strip naming the mark under the pointer. No legend: every mark says what it is.
 *
 *   <MapChart xTitle="VOL" yTitle="RET" curves={[{ name: "FRONTIER", pts }]}
 *             points={[{ name: "GMV", x: 0.06, y: 0.04, mark: "fill" }]} rest="GMV" />
 *
 * Marks: "fill" (ink square), "dim" (ink-2 square), "open" (hollow ink-3 square),
 * "cross" (ink plus), "frame" (a 14px square drawn around the point). Tones are ink only;
 * a map of estimates has no losses to mark in signal.
 */
import { useMemo, useState } from "react";
import { formatTick, formatValue, niceTicks, type ValueFormat } from "./scales";
import { MONO_CH } from "./UPlot";
import { placeLabels } from "./map";
import { useChartTheme } from "./theme";
import { useWidth } from "./useWidth";

export interface MapCurve {
  name: string;
  pts: { x: number; y: number }[];
  dash?: boolean;
  /** Heavier stroke (the frontier itself). */
  heavy?: boolean;
}

export interface MapPoint {
  name: string;
  x: number;
  y: number;
  mark: "fill" | "dim" | "open" | "cross" | "frame";
  /** Label text (defaults to name); "" draws no label. */
  label?: string;
  /** Extra readout pairs, e.g. [["SR", "0.84"]]. */
  extra?: [string, string][];
}

export interface MapChartProps {
  curves: MapCurve[];
  points: MapPoint[];
  xTitle: string;
  yTitle: string;
  xFormat?: ValueFormat;
  yFormat?: ValueFormat;
  digits?: number;
  height?: number;
  /** The point the readout shows at rest. */
  rest?: string;
  ariaLabel: string;
}

const M = { l: 46, r: 10, t: 8, b: 36 };
const STRIP = 20;
const CH = MONO_CH * (10 / 11);

export function MapChart({ curves, points, xTitle, yTitle, xFormat = "pct", yFormat = "pct", digits = 1, height = 440, rest, ariaLabel }: MapChartProps) {
  const t = useChartTheme();
  const [box, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const plotH = height - STRIP;

  const geo = useMemo(() => {
    const xs = [...curves.flatMap((c) => c.pts.map((p) => p.x)), ...points.map((p) => p.x)].filter(Number.isFinite);
    const ys = [...curves.flatMap((c) => c.pts.map((p) => p.y)), ...points.map((p) => p.y)].filter(Number.isFinite);
    if (!xs.length || !ys.length || width < 80) return null;
    const xt = niceTicks(Math.min(0, ...xs), Math.max(...xs), Math.max(3, Math.round((width - M.l) / 90)));
    const yt = niceTicks(Math.min(...ys, 0), Math.max(...ys), Math.max(3, Math.round(plotH / 60)));
    const x0 = xt[0], x1 = xt[xt.length - 1], y0 = yt[0], y1 = yt[yt.length - 1];
    const W = width - M.l - M.r;
    const H = plotH - M.t - M.b;
    const X = (v: number) => M.l + ((v - x0) / (x1 - x0 || 1)) * W;
    const Y = (v: number) => M.t + H - ((v - y0) / (y1 - y0 || 1)) * H;
    const labelled = points.map((p, i) => ({ p, i })).filter(({ p }) => (p.label ?? p.name) !== "");
    const placed = placeLabels(
      labelled.map(({ p }) => ({ x: X(p.x) - M.l, y: Y(p.y) - M.t, w: (p.label ?? p.name).length * CH + 2, h: 11 })),
      { w: W, h: H },
    );
    return { xt, yt, X, Y, W, H, labels: labelled.map(({ p, i }, k) => ({ i, text: p.label ?? p.name, x: placed[k].x + M.l, y: placed[k].y + M.t })) };
  }, [curves, points, width, plotH]);

  const shown = hover ?? Math.max(0, points.findIndex((p) => p.name === rest));
  const sp = points[shown];

  const move = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!geo) return;
    const r = e.currentTarget.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    let best = -1, bd = 22 * 22;
    points.forEach((p, i) => {
      const d = (geo.X(p.x) - mx) ** 2 + (geo.Y(p.y) - my) ** 2;
      if (d < bd) {
        bd = d;
        best = i;
      }
    });
    setHover(best >= 0 ? best : null);
  };

  const font = (px: number) => ({ fontSize: px, fontFamily: t.mono });
  const step = (ts: number[]) => (ts.length > 1 ? Math.abs(ts[1] - ts[0]) : 1);
  return (
    <div ref={box} className="oc-chart oc-chart-map">
      <div className="oc-chart-readout num" aria-live="off">
        {sp && (
          <>
            <span className="oc-chart-readout-k">{sp.name}</span> · <span className="oc-chart-readout-k">{yTitle}</span> <span className="oc-chart-readout-v">{formatValue(sp.y, yFormat, digits)}</span> ·{" "}
            <span className="oc-chart-readout-k">{xTitle}</span> <span className="oc-chart-readout-v">{formatValue(sp.x, xFormat, digits)}</span>
            {sp.extra?.map(([k, v]) => (
              <span key={k}>
                {" · "}
                <span className="oc-chart-readout-k">{k}</span> <span className="oc-chart-readout-v">{v}</span>
              </span>
            ))}
          </>
        )}
      </div>
      {geo && (
        <svg width={width} height={plotH} viewBox={`0 0 ${width} ${plotH}`} role="img" aria-label={ariaLabel} onMouseMove={move} onMouseLeave={() => setHover(null)}>
          {geo.yt.map((v) => (
            <g key={`y${v}`}>
              <line x1={M.l} x2={M.l + geo.W} y1={Math.round(geo.Y(v)) + 0.5} y2={Math.round(geo.Y(v)) + 0.5} stroke={Math.abs(v) < 1e-12 ? t.ink : t.ink3} strokeDasharray={Math.abs(v) < 1e-12 ? undefined : "1 3"} />
              <text x={M.l - 6} y={geo.Y(v)} textAnchor="end" dominantBaseline="middle" fill={t.ink2} {...font(11)}>
                {formatTick(v, yFormat, step(geo.yt))}
              </text>
            </g>
          ))}
          {geo.xt.map((v) => (
            <g key={`x${v}`}>
              <line x1={Math.round(geo.X(v)) + 0.5} x2={Math.round(geo.X(v)) + 0.5} y1={M.t} y2={M.t + geo.H} stroke={t.ink3} strokeDasharray="1 3" />
              <text x={geo.X(v)} y={M.t + geo.H + 15} textAnchor="middle" fill={t.ink2} {...font(11)}>
                {formatTick(v, xFormat, step(geo.xt))}
              </text>
            </g>
          ))}
          <line x1={M.l} x2={M.l + geo.W} y1={M.t + geo.H + 0.5} y2={M.t + geo.H + 0.5} stroke={t.ink} />
          <text x={M.l + geo.W} y={plotH - 3} textAnchor="end" fill={t.ink2} {...font(11)}>
            {xTitle} →
          </text>
          <text x={M.l + 4} y={M.t + 10} fill={t.ink2} {...font(11)}>
            ↑ {yTitle}
          </text>
          {curves.map((c) => (
            <polyline
              key={c.name}
              points={c.pts.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y)).map((p) => `${geo.X(p.x)},${geo.Y(p.y)}`).join(" ")}
              fill="none"
              stroke={c.heavy ? t.ink : t.ink2}
              strokeWidth={c.heavy ? 1.75 : 1.25}
              strokeDasharray={c.dash ? "5 4" : undefined}
            />
          ))}
          {points.map((p, i) => {
            const cx = geo.X(p.x), cy = geo.Y(p.y);
            const hot = i === hover;
            if (p.mark === "frame") return <rect key={i} x={cx - 7} y={cy - 7} width={14} height={14} fill="none" stroke={t.ink} strokeWidth={hot ? 2.5 : 1.5} />;
            if (p.mark === "cross")
              return (
                <g key={i} stroke={t.ink} strokeWidth={hot ? 2.5 : 1.75}>
                  <line x1={cx - 5} x2={cx + 5} y1={cy} y2={cy} />
                  <line x1={cx} x2={cx} y1={cy - 5} y2={cy + 5} />
                </g>
              );
            const s = p.mark === "fill" ? 7 : 6;
            const fill = p.mark === "fill" ? t.ink : p.mark === "dim" ? t.ink2 : t.paper;
            const stroke = p.mark === "open" ? t.ink2 : fill;
            return <rect key={i} x={cx - s / 2} y={cy - s / 2} width={s} height={s} fill={fill} stroke={stroke} strokeWidth={hot ? 2 : 1} />;
          })}
          {geo.labels.map((l) => {
            const p = points[l.i];
            return (
              <text key={l.i} x={l.x + 1} y={l.y + 5.5} dominantBaseline="middle" fill={p.mark === "open" ? t.ink2 : t.ink} {...font(10)}>
                {l.text}
              </text>
            );
          })}
          {curves
            .filter((c) => c.pts.length > 1)
            .map((c) => {
              const last = c.pts[c.pts.length - 1];
              const x = Math.min(geo.X(last.x) + 4, M.l + geo.W - c.name.length * CH);
              return (
                <text key={`l${c.name}`} x={x} y={Math.max(M.t + 8, geo.Y(last.y) - 8)} fill={c.heavy ? t.ink : t.ink2} {...font(10)}>
                  {c.name}
                </text>
              );
            })}
        </svg>
      )}
    </div>
  );
}
