/**
 * Lines, points and pre-binned bars on one numeric (or time) x axis, on uPlot.
 *
 *   <XYChart x={days} series={[{ name: "GJR", y, tone: "ink" }]} hlines={[{ y: 0.18, label: "LONG RUN" }]} />
 *   <XYChart time x={dates} series={[{ name: "RET", y, mode: "points", tone: "ink3" },
 *                                    { name: "BREACH", y: b, mode: "points", tone: "signal" }]} />
 *   <XYChart logX xTicks={[{ at: 30, label: "30D" }]} x={strikes} series={[{ name: "MID", y, mode: "points", lo: bid, hi: ask }]} />
 *
 * For the shapes the presets do not cover: a fitted density over a histogram, a forecast term
 * structure with reference levels, daily P&L dots against a VaR band. House style as the
 * presets: tones are ink / ink-2 / ink-3 / signal only (signal means a loss or a breach),
 * dotted --ink-3 grid, mono ticks, a readout strip, direct end labels on lines, no legend.
 */
import { useMemo, useRef, type CSSProperties } from "react";
import uPlot from "uplot";
import { endLabels, formatTick, formatValue, niceTicks, toEpochSec, type ValueFormat } from "./scales";
import { DASH, MONO_CH, Readout, STRIP, UPlot, axisFont, niceRange, pxr, writeReadout, xRule, yAxis } from "./UPlot";
import { useChartTheme, type ChartTheme } from "./theme";

export type Tone = "ink" | "ink2" | "ink3" | "signal";

export interface XYSeries {
  name: string;
  y: (number | null)[];
  mode?: "line" | "points" | "bars";
  tone?: Tone;
  dash?: "solid" | "dot" | "dash";
  width?: number;
  /** Point diameter in CSS px (points mode). */
  size?: number;
  /** Direct end label (lines default on). */
  label?: boolean;
  /** Lines: draw across missing points (a sparse series on a shared x). */
  span?: boolean;
  /** Points mode: a vertical hairline whisker from lo to hi at each point (e.g. bid to ask). */
  lo?: (number | null)[];
  hi?: (number | null)[];
}

export interface XYRule {
  at: number;
  label: string;
  tone?: Tone;
  dash?: "solid" | "dot" | "dash";
}

export interface XYChartProps {
  x: (number | string)[];
  series: XYSeries[];
  /** x values are dates (strings or epoch ms): a time axis. */
  time?: boolean;
  xFormat?: ValueFormat;
  yFormat?: ValueFormat;
  digits?: number;
  logY?: boolean;
  hlines?: XYRule[];
  vlines?: XYRule[];
  height?: number;
  xTitle?: string;
  ariaLabel: string;
  /** Include zero in the y range. */
  zero?: boolean;
  /** Log axis floor (values below are clipped out of view), e.g. 0.5 day for counts. */
  yMin?: number;
  /** Log x axis (x > 0 only). */
  logX?: boolean;
  /** Explicit x ticks and their labels (e.g. horizons "10D" "1M"); replaces the computed ones. */
  xTicks?: { at: number; label: string }[];
  /** Shaded x spans behind the series (NBER recessions, inversions, a regime): "faint" fills
   *  --paper-2, "hatch" draws --ink-3 diagonal hairlines. Dates on a time axis. */
  bands?: XYBand[];
}

export interface XYBand {
  from: number | string;
  to: number | string;
  tone?: "faint" | "hatch";
}

/** Bands as ordered x pairs in axis units (unix seconds on a time axis); unparseable spans dropped. */
export function xyBands(bands: XYBand[], time: boolean): { x0: number; x1: number; tone: "faint" | "hatch" }[] {
  const conv = (v: number | string) => (time ? toEpochSec(v) : typeof v === "number" ? v : Number(v));
  const out: { x0: number; x1: number; tone: "faint" | "hatch" }[] = [];
  for (const b of bands) {
    const a = conv(b.from);
    const c = conv(b.to);
    if (a === null || c === null || !Number.isFinite(a) || !Number.isFinite(c)) continue;
    out.push({ x0: Math.min(a, c), x1: Math.max(a, c), tone: b.tone ?? "faint" });
  }
  return out;
}

const toneColor = (t: ChartTheme, tone: Tone | undefined) => (tone === "signal" ? t.signal : tone === "ink2" ? t.ink2 : tone === "ink3" ? t.ink3 : t.ink);
const MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

export function xyData(x: (number | string)[], series: XYSeries[], time: boolean, logY: boolean): uPlot.AlignedData {
  const xs = x.map((v) => (time ? toEpochSec(v) : typeof v === "number" ? v : Number(v)));
  const keep = xs.map((v, i) => (v !== null && Number.isFinite(v) ? i : -1)).filter((i) => i >= 0);
  const clean = (v: number | null | undefined) => (typeof v === "number" && Number.isFinite(v) && (!logY || v > 0) ? v : null);
  return [keep.map((i) => xs[i] as number), ...series.map((s) => keep.map((i) => clean(s.y[i])))];
}

/** Whisker ends per series, aligned with the x values xyData keeps; null for a series without them. */
export function xyWhiskers(x: (number | string)[], series: XYSeries[], time = false): ({ lo: (number | null)[]; hi: (number | null)[] } | null)[] {
  const xs = x.map((v) => (time ? toEpochSec(v) : typeof v === "number" ? v : Number(v)));
  const keep = xs.map((v, i) => (v !== null && Number.isFinite(v) ? i : -1)).filter((i) => i >= 0);
  const clean = (v: number | null | undefined) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  return series.map((s) => (s.lo || s.hi ? { lo: keep.map((i) => clean(s.lo?.[i])), hi: keep.map((i) => clean(s.hi?.[i])) } : null));
}

/** A log x range padded by a tenth of a decade on the left; the right edge is the last x, so line
 *  ends reach the gutter and their direct labels are spaced there, not overprinted in the plot. */
export function logXRange(min: number, max: number): [number, number] {
  const lo = Math.max(min, 1e-12);
  const hi = Math.max(max, lo);
  return [lo / 10 ** 0.1, hi > lo ? hi : hi * 10 ** 0.1];
}

export function XYChart({ x, series, time = false, xFormat = "num", yFormat = "num", digits = 2, logY = false, hlines = [], vlines = [], height = 280, xTitle, ariaLabel, zero, yMin, logX = false, xTicks, bands }: XYChartProps) {
  const t = useChartTheme();
  const readout = useRef<HTMLDivElement>(null);
  const data = useMemo(() => xyData(x, series, time, logY), [x, series, time, logY]);
  const whiskers = useMemo(() => xyWhiskers(x, series, time), [x, series, time]);
  const whiskerRef = useRef(whiskers);
  whiskerRef.current = whiskers;
  const metaKey = JSON.stringify(series.map((s) => [s.name, s.mode ?? "line", s.tone, s.dash, s.width, s.size, s.label, s.span ?? false]));
  const rulesKey = JSON.stringify([hlines, vlines, xTicks ?? null]);
  const bandsKey = JSON.stringify(bands ? xyBands(bands, time) : []);
  const plotH = height - STRIP;

  const opts = useMemo(() => {
    const meta = JSON.parse(metaKey) as [string, "line" | "points" | "bars", Tone | undefined, string | undefined, number | undefined, number | undefined, boolean | undefined, boolean][];
    const [hl, vl, xt] = JSON.parse(rulesKey) as [XYRule[], XYRule[], { at: number; label: string }[] | null];
    const bd = JSON.parse(bandsKey) as ReturnType<typeof xyBands>;
    const colors = meta.map((m) => toneColor(t, m[2]));
    const labelled = meta.map((m) => m[6] ?? m[1] === "line");
    const count = Math.max(2, Math.round(plotH / 56));
    const r = pxr();
    return (width: number) => {
      const longest = Math.max(0, ...meta.filter((_, i) => labelled[i]).map((m) => Math.min(14, m[0].length)), ...hl.map((h) => Math.min(14, h.label.length)));
      const gutter = longest ? Math.min(Math.round(width * 0.3), Math.ceil(longest * MONO_CH) + 14) : 8;
      const xCount = Math.max(2, Math.round((width - gutter) / 90));
      const show = (u: uPlot, idx: number | null | undefined) => {
        const xs = u.data[0];
        if (!xs.length) return writeReadout(readout.current, [{ k: "NO DATA", v: "" }]);
        // at rest: the latest point on a time axis, else the peak of the first series
        const first = u.data[1] as (number | null)[] | undefined;
        const peak = first ? first.reduce<number>((b, v, k) => (v != null && (first[b] == null || v > (first[b] as number)) ? k : b), 0) : xs.length - 1;
        const i = idx ?? (time ? xs.length - 1 : peak);
        const xv = xs[i];
        const d = new Date(xv * 1000);
        const xs0 = time ? `${String(d.getUTCDate()).padStart(2, "0")} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}` : formatValue(xv, xFormat, digits);
        writeReadout(readout.current, [
          { k: time ? "DATE" : (xTitle ?? "X").toUpperCase(), v: xs0 },
          ...meta
            .map((m, s) => ({ k: m[0], v: u.data[s + 1][i] as number | null | undefined, signal: m[2] === "signal" }))
            .filter((it) => it.v !== null && it.v !== undefined)
            .map((it) => ({ k: it.k, v: formatValue(it.v, yFormat, digits), signal: it.signal })),
        ]);
      };
      const o: Omit<uPlot.Options, "width" | "height"> = {
        ...(time ? { tzDate: (ts: number) => uPlot.tzDate(new Date(ts * 1000), "Etc/UTC") } : {}),
        legend: { show: false },
        padding: [10, gutter, 0, 0],
        cursor: { y: false, drag: { x: false, y: false }, points: { size: 6, width: 1.25, fill: t.paper, stroke: (_u: uPlot, s: number) => colors[s - 1] ?? t.ink } },
        scales: {
          x: time
            ? { time: true }
            : logX
              ? { time: false, distr: 3, range: (_u, min, max) => logXRange(min, max) }
              : { time: false, range: (_u, min, max) => (min === max ? [min - 1, max + 1] : [min, max]) },
          y: logY
            ? { distr: 3, range: (_u, min, max) => [Math.max(yMin ?? 0, min > 0 ? 10 ** Math.floor(Math.log10(min)) : yMin ?? 1), 10 ** Math.ceil(Math.log10(Math.max(max, 1e-12)))] }
            : { range: (_u, min, max) => niceRange(min, max, count, [...(zero ? [0] : []), ...hl.map((h) => h.at)]) },
        },
        series: [
          {},
          ...meta.map((m, i) => {
            if (m[1] === "bars")
              return { label: m[0], width: 0, fill: colors[i], points: { show: false }, paths: uPlot.paths.bars!({ size: [1, Infinity, 1], gap: 1 }) };
            if (m[1] === "points")
              return { label: m[0], width: 0, stroke: colors[i], paths: () => null, points: { show: true, size: m[5] ?? 3, width: 0, fill: colors[i], stroke: colors[i] } };
            return { label: m[0], stroke: colors[i], width: m[4] ?? 1.25, dash: m[3] ? DASH[m[3]] : undefined, points: { show: false }, spanGaps: m[7] };
          }),
        ],
        axes: [
          {
            stroke: t.ink2,
            font: axisFont(t),
            gap: 4,
            size: xTitle ? 38 : 24,
            grid: { show: false },
            ticks: { show: true, stroke: t.ink, width: 1, size: 4 },
            border: { show: false },
            ...(time
              ? {
                  space: 76,
                  values: (_u: uPlot, splits: number[], _ai: number, _sp: number, incr: number) =>
                    splits.map((s) => {
                      const d = new Date(s * 1000);
                      if (incr >= 360 * 86400) return String(d.getUTCFullYear());
                      if (incr >= 28 * 86400) return `${MON[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`;
                      return `${String(d.getUTCDate()).padStart(2, "0")} ${MON[d.getUTCMonth()]}`;
                    }),
                }
              : xt
                ? {
                    splits: (_u: uPlot, _i: number, min: number, max: number) => xt.map((k) => k.at).filter((v) => v >= min - 1e-12 && v <= max + 1e-12),
                    values: (_u: uPlot, splits: number[]) => splits.map((v) => xt.find((k) => k.at === v)?.label ?? formatValue(v, xFormat, digits)),
                    filter: (_u: uPlot, splits: number[]) => splits,
                  }
                : {
                    splits: (_u: uPlot, _i: number, min: number, max: number) => niceTicks(min, max, xCount).filter((v) => v >= min - 1e-12 && v <= max + 1e-12),
                    values: (_u: uPlot, splits: number[]) => splits.map((v) => formatTick(v, xFormat, splits.length > 1 ? Math.abs(splits[1] - splits[0]) : 1)),
                  }),
            ...(xTitle ? { label: xTitle.toUpperCase(), labelFont: `11px ${t.mono}`, labelSize: 14 } : {}),
          },
          logY
            ? {
                ...yAxis(t, yFormat, count, true),
                // decades only: uPlot's default log splits rule every 1-9 step
                splits: (_u: uPlot, _i: number, min: number, max: number) => {
                  const out: number[] = [];
                  for (let e = Math.ceil(Math.log10(min) - 1e-9); 10 ** e <= max * (1 + 1e-9); e++) out.push(10 ** e);
                  return out;
                },
                filter: (_u: uPlot, splits: number[]) => splits,
              }
            : yAxis(t, yFormat, count, false),
        ],
        hooks: {
          ready: [(u) => show(u, null)],
          setData: [(u) => show(u, null)],
          setCursor: [(u) => show(u, u.cursor.idx)],
          drawAxes: [
            (u) => {
              if (!bd.length) return;
              const { ctx, bbox } = u;
              ctx.save();
              const clip = new Path2D();
              clip.rect(bbox.left, bbox.top, bbox.width, bbox.height);
              ctx.clip(clip);
              for (const b of bd) {
                const a = u.valToPos(b.x0, "x", true);
                const c = u.valToPos(b.x1, "x", true);
                const x0 = Math.max(bbox.left, Math.min(a, c));
                const w = Math.max(r, Math.min(bbox.left + bbox.width, Math.max(a, c)) - x0);
                if (x0 > bbox.left + bbox.width) continue;
                if (b.tone === "hatch") {
                  ctx.save();
                  const p = new Path2D();
                  p.rect(x0, bbox.top, w, bbox.height);
                  ctx.clip(p);
                  ctx.strokeStyle = t.ink3;
                  ctx.globalAlpha = 0.55;
                  ctx.lineWidth = r;
                  ctx.setLineDash([]);
                  ctx.beginPath();
                  const step = 5 * r;
                  for (let x = x0 - bbox.height; x < x0 + w; x += step) {
                    ctx.moveTo(x, bbox.top + bbox.height);
                    ctx.lineTo(x + bbox.height, bbox.top);
                  }
                  ctx.stroke();
                  ctx.restore();
                } else {
                  ctx.fillStyle = t.paper2;
                  ctx.fillRect(x0, bbox.top, w, bbox.height);
                }
              }
              ctx.restore();
            },
          ],
          draw: [
            (u) => {
              xRule(u, t);
              const { ctx, bbox } = u;
              const right = bbox.left + bbox.width;
              ctx.save();
              // whiskers: a hairline from lo to hi at each point, in the series' tone
              ctx.save();
              const clip = new Path2D();
              clip.rect(bbox.left, bbox.top, bbox.width, bbox.height);
              ctx.clip(clip);
              whiskerRef.current.forEach((w, s) => {
                if (!w) return;
                ctx.strokeStyle = colors[s];
                ctx.lineWidth = r;
                ctx.setLineDash([]);
                ctx.beginPath();
                u.data[0].forEach((xv, i) => {
                  const lo = w.lo[i];
                  const hi = w.hi[i];
                  if (lo === null || hi === null) return;
                  const px = Math.round(u.valToPos(xv, "x", true)) + 0.5;
                  if (px < bbox.left || px > right) return;
                  ctx.moveTo(px, u.valToPos(lo, "y", true));
                  ctx.lineTo(px, u.valToPos(hi, "y", true));
                });
                ctx.stroke();
              });
              ctx.restore();
              ctx.font = `${Math.round(11 * r)}px ${t.mono}`;
              ctx.textBaseline = "middle";
              // reference levels: a dotted rule across, labelled in the right gutter
              const items: { y: number; text: string; x: number; color: string }[] = [];
              for (const h of hl) {
                const y = Math.round(u.valToPos(h.at, "y", true)) + 0.5;
                if (y < bbox.top - 1 || y > bbox.top + bbox.height + 1) continue;
                const c = toneColor(t, h.tone ?? "ink2");
                ctx.strokeStyle = c;
                ctx.lineWidth = r;
                ctx.setLineDash((DASH[h.dash ?? "dot"] ?? []).map((v) => v * r));
                ctx.beginPath();
                ctx.moveTo(bbox.left, y);
                ctx.lineTo(right, y);
                ctx.stroke();
                items.push({ y, text: h.label, x: right, color: c });
              }
              ctx.textBaseline = "bottom";
              const taken: [number, number][] = [];
              for (const v of vl) {
                const xp = Math.round(u.valToPos(v.at, "x", true)) + 0.5;
                if (xp < bbox.left - 1 || xp > right + 1) continue;
                const c = toneColor(t, v.tone ?? "signal");
                ctx.strokeStyle = c;
                ctx.lineWidth = r;
                ctx.setLineDash((DASH[v.dash ?? "solid"] ?? []).map((q) => q * r));
                ctx.beginPath();
                ctx.moveTo(xp, bbox.top);
                ctx.lineTo(xp, bbox.top + bbox.height);
                ctx.stroke();
                ctx.fillStyle = c;
                const tw = ctx.measureText(v.label).width;
                const toRight = xp + 4 * r + tw <= right;
                ctx.textAlign = toRight ? "left" : "right";
                const x0 = toRight ? xp + 4 * r : xp - 4 * r - tw;
                // a label that would overprint an earlier one drops one line into the plot
                const row = taken.some(([a, b]) => x0 < b && x0 + tw > a) ? 1 : 0;
                taken.push([x0, x0 + tw]);
                ctx.fillText(v.label, toRight ? xp + 4 * r : xp - 4 * r, bbox.top - 1 * r + row * 13 * r);
              }
              ctx.setLineDash([]);
              ctx.textBaseline = "middle";
              // end labels on lines
              const xs = u.data[0];
              meta.forEach((m, s) => {
                if (!labelled[s]) return;
                const ys = u.data[s + 1];
                const ymin = u.scales.y.min ?? -Infinity;
                const ymax = u.scales.y.max ?? Infinity;
                for (let i = xs.length - 1; i >= 0; i--) {
                  const v = ys[i];
                  if (v !== null && v !== undefined && v >= ymin && v <= ymax) {
                    const px = u.valToPos(xs[i], "x", true);
                    const py = u.valToPos(v, "y", true);
                    if (px < right - 48 * r) {
                      // the line ends inside the plot: label it where it ends, no leader across the plot
                      ctx.fillStyle = colors[s] === t.ink3 ? t.ink2 : colors[s];
                      ctx.textAlign = "left";
                      ctx.fillText(m[0], px + 4 * r, Math.max(bbox.top + 6 * r, py - 7 * r));
                    } else items.push({ y: py, text: m[0], x: px, color: colors[s] });
                    break;
                  }
                }
              });
              const keyed = items.map((it, k) => ({ y: it.y, text: String(k) }));
              const placed = endLabels(keyed, 12 * r);
              const floor = bbox.top + bbox.height;
              if (placed.length && placed[placed.length - 1].y > floor) {
                placed[placed.length - 1].y = floor;
                for (let i = placed.length - 2; i >= 0; i--) placed[i].y = Math.min(placed[i].y, placed[i + 1].y - 12 * r);
              }
              const maxChars = Math.max(3, Math.floor((gutter - 14) / MONO_CH));
              ctx.textAlign = "left";
              for (const p of placed) {
                const it = items[Number(p.text)];
                const text = it.text.length > maxChars ? it.text.slice(0, maxChars - 1) + "…" : it.text;
                ctx.strokeStyle = it.color;
                ctx.lineWidth = r;
                ctx.beginPath();
                ctx.moveTo(Math.min(it.x, right), it.y);
                ctx.lineTo(right + 3 * r, it.y);
                ctx.lineTo(right + 6 * r, p.y);
                ctx.stroke();
                ctx.fillStyle = it.color === t.ink3 ? t.ink2 : it.color;
                ctx.fillText(text, right + 8 * r, p.y);
              }
              ctx.restore();
            },
          ],
        },
      };
      return o;
    };
  }, [t, metaKey, rulesKey, bandsKey, plotH, time, xFormat, yFormat, digits, logY, zero, xTitle, yMin, logX]);

  return (
    <div className="oc-chart oc-chart-uplot oc-chart-fixed" style={{ "--chart-h": `${height}px` } as CSSProperties} role="img" aria-label={ariaLabel}>
      <Readout refEl={readout} />
      <UPlot opts={opts} data={data} height={plotH} />
    </div>
  );
}
