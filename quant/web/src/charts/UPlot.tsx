/**
 * The paper tape charts, on uPlot (canvas) and plain SVG. Plotly is not used here.
 *
 *   <TimeSeriesChart series={[{ name: "SPY", x, y }]} yFormat="usd" rangeSelector />
 *   <BarChart x={labels} y={values} yFormat="pct" colorBySign />
 *   <HistogramChart values={returns} format="pct" vlines={[{ x: q05, label: "5%" }]} />
 *   <HeatmapChart x={cols} y={rows} z={matrix} format="pct" diverging />
 *
 * House style: ink strokes at 1.25px, dotted --ink-3 gridlines, IBM Plex Mono 11px axes
 * with a true minus, a 20px crosshair readout strip above the plot (it shows the latest
 * values until the pointer moves over the plot), and direct end-of-line labels instead of
 * a legend. Areas fill to their baseline with a hatch, never a gradient. Losses and
 * negative bars are --signal; gains are ink.
 *
 * The props match the old Plotly presets so every page compiles unchanged. A Plotly
 * `layout` override is read only for what still means something here: yaxis.range,
 * yaxis.rangemode "tozero", the heatmap's axis titles, its yaxis.autorange (row order)
 * and rect shapes in category coordinates. Everything else in `layout` is ignored.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { alignSeries, endLabels, formatTick, formatValue, niceTicks, type ValueFormat } from "./scales";
import { heatScale } from "./heat";
import { labelColor, luminance, resolveColor, slotColor, useChartTheme, type ChartTheme } from "./theme";

export const STRIP = 20; // crosshair readout strip
const RANGE_ROW = 28; // 1M 3M 1Y 5Y MAX
export const MONO_CH = 6.7; // IBM Plex Mono advance at 11px, for layout before the canvas exists
const DAY = 86400;

export const pxr = () => (typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1);

// =================================================================== wrapper

type OptsBuilder = (width: number) => Omit<uPlot.Options, "width" | "height">;

/**
 * A React wrapper around one uPlot instance: created on mount (and again when `opts` or
 * `height` change, e.g. on a theme flip), `setData` when only the data changes, `setSize`
 * from a ResizeObserver, destroyed on unmount.
 */
export function UPlot({ opts, data, height, className }: { opts: OptsBuilder; data: uPlot.AlignedData; height: number; className?: string }) {
  const el = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const latest = useRef(data);

  useEffect(() => {
    latest.current = data;
    plot.current?.setData(data);
  }, [data]);

  useEffect(() => {
    const node = el.current;
    if (!node) return;
    const width = Math.max(120, node.clientWidth || 600);
    const u = new uPlot({ ...opts(width), width, height }, latest.current, node);
    plot.current = u;
    const ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0]?.contentRect.width ?? 0);
      if (w > 0 && Math.abs(w - u.width) >= 1) u.setSize({ width: w, height });
    });
    ro.observe(node);
    // Canvas text drawn before the web fonts land uses the fallback face; redraw once they do.
    let live = true;
    document.fonts?.ready.then(() => live && u.redraw(false, true));
    return () => {
      live = false;
      ro.disconnect();
      u.destroy();
      plot.current = null;
    };
  }, [opts, height]);

  return <div ref={el} className={`oc-uplot ${className ?? ""}`} style={{ height }} />;
}

// =================================================================== shared pieces

export interface ReadoutItem {
  k: string;
  v: string;
  signal?: boolean;
}

/** Write the readout strip: KEY value · KEY value. Text nodes only (series names are data). */
export function writeReadout(node: HTMLElement | null, items: ReadoutItem[]) {
  if (!node) return;
  node.replaceChildren();
  items.forEach((it, i) => {
    if (i) node.append(document.createTextNode(" · "));
    const k = document.createElement("span");
    k.className = "oc-chart-readout-k";
    k.textContent = it.k;
    const v = document.createElement("span");
    v.className = it.signal ? "oc-chart-readout-v oc-chart-readout-neg" : "oc-chart-readout-v";
    v.textContent = it.v;
    node.append(k, document.createTextNode(" "), v);
  });
}

export function Readout({ refEl }: { refEl: RefObject<HTMLDivElement> }) {
  return <div ref={refEl} className="oc-chart-readout" aria-live="off" />;
}

function useElementWidth(ref: RefObject<HTMLElement>): number {
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    setW(node.clientWidth);
    const ro = new ResizeObserver((entries) => setW(Math.round(entries[0]?.contentRect.width ?? 0)));
    ro.observe(node);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

/** The parts of a Plotly `layout` override that still apply. */
function yHints(layout: unknown): { fixed?: [number, number]; toZero: boolean } {
  const y = (layout as any)?.yaxis;
  const r = y?.range;
  const fixed = Array.isArray(r) && r.length === 2 && r.every((v: unknown) => typeof v === "number" && Number.isFinite(v)) ? ([r[0], r[1]] as [number, number]) : undefined;
  return { fixed, toZero: y?.rangemode === "tozero" };
}

export const DASH: Record<string, number[] | undefined> = { solid: undefined, dot: [1.5, 3], dash: [5, 4], dashdot: [5, 3, 1.5, 3] };

export function axisFont(t: ChartTheme) {
  return `11px ${t.mono}`;
}

function ymdUTC(sec: number, withTime: boolean): string {
  const d = new Date(sec * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  const day = `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`;
  return withTime ? `${day} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())} UTC` : day;
}

const MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

function timeTicks(splits: number[], incr: number): string[] {
  const p = (n: number) => String(n).padStart(2, "0");
  return splits.map((s) => {
    const d = new Date(s * 1000);
    if (incr >= 360 * DAY) return String(d.getUTCFullYear());
    if (incr >= 28 * DAY) return `${MON[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`;
    if (incr >= DAY) return `${p(d.getUTCDate())} ${MON[d.getUTCMonth()]}`;
    return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  });
}

/** Y range snapped to nice tick ends; includes the baseline (and zero when asked). */
export function niceRange(dmin: number | null, dmax: number | null, count: number, extra: number[], fixed?: [number, number]): [number, number] {
  if (fixed) return fixed;
  const vals = [dmin, dmax, ...extra].filter((v): v is number => typeof v === "number" && Number.isFinite(v));
  if (!vals.length) return [0, 1];
  const ticks = niceTicks(Math.min(...vals), Math.max(...vals), count);
  return ticks.length >= 2 ? [ticks[0], ticks[ticks.length - 1]] : [Math.min(...vals), Math.max(...vals)];
}

function ticksWithin(min: number, max: number, count: number): number[] {
  const eps = (max - min) * 1e-9;
  return niceTicks(min, max, count).filter((v) => v >= min - eps && v <= max + eps);
}

export function yAxis(t: ChartTheme, fmt: ValueFormat, count: number, log: boolean, title?: string): uPlot.Axis {
  return {
    side: 3,
    stroke: t.ink2,
    font: axisFont(t),
    gap: 6,
    grid: { stroke: t.ink3, width: 1, dash: [1, 3] },
    ticks: { show: false },
    splits: log ? undefined : (_u, _i, min, max) => ticksWithin(min, max, count),
    values: (_u, splits) => {
      const step = splits.length > 1 ? Math.abs(splits[1] - splits[0]) : Math.abs(splits[0] || 1);
      return splits.map((v) => formatTick(v, fmt, log ? Math.abs(v) || step : step));
    },
    size: (_u, values) => Math.ceil(Math.max(3, ...(values ?? []).map((v) => String(v).length)) * MONO_CH) + 10,
    ...(title ? { label: title.toUpperCase(), labelFont: `11px ${t.mono}`, labelSize: 14 } : {}),
  };
}

/** The x baseline: a solid 1px ink rule along the bottom of the plot, over the gridlines. */
export function xRule(u: uPlot, t: ChartTheme) {
  const { ctx, bbox } = u;
  const y = Math.round(bbox.top + bbox.height) + 0.5;
  ctx.save();
  ctx.setLineDash([]);
  ctx.strokeStyle = t.ink;
  ctx.lineWidth = pxr();
  ctx.beginPath();
  ctx.moveTo(bbox.left, y);
  ctx.lineTo(bbox.left + bbox.width, y);
  ctx.stroke();
  ctx.restore();
}

/** Diagonal hairlines inside `clip`, in canvas pixels. */
function hatch(ctx: CanvasRenderingContext2D, clip: Path2D, box: { left: number; top: number; width: number; height: number }, color: string, alpha = 0.6) {
  const r = pxr();
  ctx.save();
  ctx.setLineDash([]);
  ctx.clip(clip);
  ctx.strokeStyle = color;
  ctx.globalAlpha = alpha;
  ctx.lineWidth = r;
  ctx.beginPath();
  const step = 5 * r;
  for (let x = box.left - box.height; x < box.left + box.width; x += step) {
    ctx.moveTo(x, box.top + box.height);
    ctx.lineTo(x + box.height, box.top);
  }
  ctx.stroke();
  ctx.restore();
}

/** The negative mark on a neutral heatmap cell: a back-slash hatch, the mirror of the missing-cell hatch. */
function backHatch(ctx: CanvasRenderingContext2D, clip: Path2D, box: { left: number; top: number; width: number; height: number }, color: string) {
  const r = pxr();
  ctx.save();
  ctx.setLineDash([]);
  ctx.clip(clip);
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.55;
  ctx.lineWidth = r;
  ctx.beginPath();
  const step = 4 * r;
  for (let x = box.left; x < box.left + box.width + box.height; x += step) {
    ctx.moveTo(x - box.height, box.top);
    ctx.lineTo(x, box.top + box.height);
  }
  ctx.stroke();
  ctx.restore();
}

// =================================================================== time series

export interface LineSeries {
  name: string;
  x: (string | number)[];
  y: (number | null)[];
  color?: string;
  dash?: "solid" | "dot" | "dash" | "dashdot";
  width?: number;
  /** Fill to the baseline (hatched). */
  fill?: boolean;
  /** Never dual axes. Use two charts. */
  hoverSuffix?: string;
  customdata?: unknown[];
}

const RANGES: { key: string; days: number }[] = [
  { key: "1M", days: 31 },
  { key: "3M", days: 92 },
  { key: "1Y", days: 366 },
  { key: "5Y", days: 1827 },
];

export interface TimeSeriesChartProps {
  series: LineSeries[];
  yFormat?: ValueFormat;
  digits?: number;
  area?: boolean;
  rangeSelector?: boolean;
  logY?: boolean;
  height?: number;
  yTitle?: string;
  baseline?: number;
  showLegend?: boolean;
  compact?: boolean;
  /** Plotly layout override; only yaxis.range and yaxis.rangemode "tozero" are read. */
  layout?: Partial<import("plotly.js").Layout>;
}

/** Line / area time series on one y-axis. `rangeSelector` adds 1M 3M 1Y 5Y MAX; `baseline` draws a 1px ink rule at that level. */
export function TimeSeriesChart({ series, yFormat = "num", digits = 2, area, rangeSelector, logY, height = 320, yTitle, baseline, showLegend, compact, layout }: TimeSeriesChartProps) {
  const t = useChartTheme();
  const readout = useRef<HTMLDivElement>(null);
  const [range, setRange] = useState("MAX");
  const aligned = useMemo(() => alignSeries(series), [series]);
  const span = aligned.t.length > 1 ? aligned.t[aligned.t.length - 1] - aligned.t[0] : 0;
  const ranges = RANGES.filter((r) => r.days * DAY < span);
  const active = range !== "MAX" && ranges.some((r) => r.key === range) ? range : "MAX";

  const data = useMemo<uPlot.AlignedData>(() => {
    let lo = 0;
    const days = RANGES.find((r) => r.key === active)?.days;
    if (days && aligned.t.length) {
      const cut = aligned.t[aligned.t.length - 1] - days * DAY;
      lo = Math.max(0, aligned.t.findIndex((v) => v >= cut));
    }
    const ys = aligned.ys.map((y) => (lo ? y.slice(lo) : y)).map((y) => (logY ? y.map((v) => (v !== null && v > 0 ? v : null)) : y));
    return [lo ? aligned.t.slice(lo) : aligned.t, ...ys];
  }, [aligned, active, logY]);

  // Rebuild the chart (not just its data) only when what it draws changes.
  const metaKey = JSON.stringify(series.map((s) => [s.name, s.color, s.dash, s.width, s.fill]));
  const hints = yHints(layout);
  const hintKey = JSON.stringify(hints);
  const plotH = height - STRIP - (rangeSelector ? RANGE_ROW : 0);

  const opts = useMemo<OptsBuilder>(() => {
    const meta = JSON.parse(metaKey) as [string, string | undefined, string | undefined, number | undefined, boolean | undefined][];
    const h = JSON.parse(hintKey) as ReturnType<typeof yHints>;
    const colors = meta.map((m, i) => (m[1] ? resolveColor(m[1]) : slotColor(t, i)));
    const labelsOn = showLegend ?? meta.length > 1;
    const count = Math.max(2, Math.round(plotH / (compact ? 44 : 56)));
    const r = pxr();
    return (width: number) => {
      const longest = Math.min(16, Math.max(0, ...meta.map((m) => m[0].length)));
      const gutter = labelsOn ? Math.min(Math.round(width * 0.28), Math.ceil(longest * MONO_CH) + 14) : 24;
      const maxChars = Math.max(3, Math.floor((gutter - 14) / MONO_CH));
      const intraday = (u: uPlot) => {
        const xs = u.data[0];
        return xs.length > 1 && xs[xs.length - 1] - xs[xs.length - 2] < DAY;
      };
      const show = (u: uPlot, idx: number | null | undefined) => {
        const xs = u.data[0];
        if (!xs.length) return writeReadout(readout.current, [{ k: "NO DATA", v: "" }]);
        const i = idx ?? xs.length - 1;
        writeReadout(readout.current, [
          { k: "DATE", v: ymdUTC(xs[i], intraday(u)) },
          ...meta.map((m, s) => {
            const v = u.data[s + 1][i] as number | null | undefined;
            return { k: m[0], v: formatValue(v, yFormat, digits) };
          }),
        ]);
      };
      return {
        tzDate: (ts: number) => uPlot.tzDate(new Date(ts * 1000), "Etc/UTC"),
        legend: { show: false },
        padding: [10, gutter, 0, 0],
        cursor: {
          y: false,
          points: { size: 6, width: 1.25, fill: t.paper, stroke: (_u: uPlot, s: number) => colors[s - 1] ?? t.ink },
          drag: { x: true, y: false },
        },
        scales: {
          x: { time: true },
          y: logY ? { distr: 3 } : { range: (_u, min, max) => niceRange(min, max, count, [...(baseline !== undefined ? [baseline] : []), ...(h.toZero ? [0] : [])], h.fixed) },
        },
        series: [
          {},
          ...meta.map((m, i) => ({
            label: m[0],
            stroke: colors[i],
            width: m[3] ?? 1.25,
            dash: m[2] ? DASH[m[2]] : undefined,
            points: { show: false },
            spanGaps: false,
          })),
        ],
        axes: [
          {
            stroke: t.ink2,
            font: axisFont(t),
            space: 76,
            gap: 4,
            size: 24,
            grid: { show: false },
            ticks: { show: true, stroke: t.ink, width: 1, size: 4 },
            border: { show: false },
            values: (_u, splits, _ai, _space, incr) => timeTicks(splits, incr),
          },
          yAxis(t, yFormat, count, !!logY, yTitle),
        ],
        hooks: {
          ready: [(u) => show(u, null)],
          setData: [(u) => show(u, null)],
          setCursor: [(u) => show(u, u.cursor.idx)],
          drawAxes: [
            (u) => {
              const { ctx, bbox } = u;
              const base = baseline ?? 0;
              meta.forEach((m, s) => {
                if (!(area || m[4])) return;
                const xs = u.data[0];
                const ys = u.data[s + 1] as (number | null)[];
                const y0 = u.valToPos(logY ? Math.max(base, u.scales.y.min ?? base) : base, "y", true);
                const clip = new Path2D();
                let open = false;
                let firstX = 0;
                let lastX = 0;
                for (let i = 0; i <= xs.length; i++) {
                  const v = i < xs.length ? ys[i] : null;
                  if (v === null || v === undefined) {
                    if (open) {
                      clip.lineTo(lastX, y0);
                      clip.lineTo(firstX, y0);
                      clip.closePath();
                      open = false;
                    }
                    continue;
                  }
                  const px = u.valToPos(xs[i], "x", true);
                  const py = u.valToPos(v, "y", true);
                  if (!open) {
                    clip.moveTo(px, y0);
                    clip.lineTo(px, py);
                    firstX = px;
                    open = true;
                  } else clip.lineTo(px, py);
                  lastX = px;
                }
                ctx.save();
                ctx.setLineDash([]);
                ctx.beginPath();
                ctx.rect(bbox.left, bbox.top, bbox.width, bbox.height);
                ctx.clip();
                hatch(ctx, clip, bbox, colors[s]);
                ctx.restore();
              });
              if (baseline !== undefined && !(logY && baseline <= 0)) {
                const y = Math.round(u.valToPos(baseline, "y", true)) + 0.5;
                if (y >= bbox.top - 1 && y <= bbox.top + bbox.height + 1) {
                  ctx.save();
                  ctx.setLineDash([]);
                  ctx.strokeStyle = t.ink;
                  ctx.lineWidth = r;
                  ctx.beginPath();
                  ctx.moveTo(bbox.left, y);
                  ctx.lineTo(bbox.left + bbox.width, y);
                  ctx.stroke();
                  ctx.restore();
                }
              }
            },
          ],
          draw: [
            (u) => {
              xRule(u, t);
              if (!labelsOn) return;
              const { ctx, bbox } = u;
              const xs = u.data[0];
              const xmax = u.scales.x.max ?? Infinity;
              const items: { y: number; text: string; s: number; x: number }[] = [];
              meta.forEach((_m, s) => {
                const ys = u.data[s + 1];
                for (let i = xs.length - 1; i >= 0; i--) {
                  const v = ys[i];
                  if (xs[i] <= xmax && v !== null && v !== undefined) {
                    items.push({ y: u.valToPos(v, "y", true), text: String(s), s, x: u.valToPos(xs[i], "x", true) });
                    break;
                  }
                }
              });
              const placed = endLabels(items.map((it) => ({ y: it.y, text: it.text })), 12 * r);
              // endLabels only pushes down; lift the stack back inside the plot if it ran off the bottom
              const floor = bbox.top + bbox.height;
              if (placed.length && placed[placed.length - 1].y > floor) {
                placed[placed.length - 1].y = floor;
                for (let i = placed.length - 2; i >= 0; i--) placed[i].y = Math.min(placed[i].y, placed[i + 1].y - 12 * r);
              }
              ctx.save();
              ctx.setLineDash([]);
              ctx.font = `${Math.round(11 * r)}px ${t.mono}`;
              ctx.textBaseline = "middle";
              ctx.textAlign = "left";
              const right = bbox.left + bbox.width;
              for (const p of placed) {
                const s = Number(p.text);
                const name = meta[s][0];
                const text = name.length > maxChars ? name.slice(0, maxChars - 1) + "\u2026" : name;
                const src = items.find((it) => it.s === s)!;
                ctx.strokeStyle = colors[s];
                ctx.lineWidth = r;
                ctx.beginPath();
                ctx.moveTo(Math.min(src.x, right), src.y);
                ctx.lineTo(right + 3 * r, src.y);
                ctx.lineTo(right + 6 * r, p.y);
                ctx.stroke();
                ctx.fillStyle = labelColor(t, colors[s]);
                ctx.fillText(text, right + 8 * r, p.y);
              }
              ctx.restore();
            },
          ],
        },
      };
    };
  }, [t, metaKey, hintKey, showLegend, plotH, compact, logY, baseline, area, yFormat, digits, yTitle]);

  const names = series.map((s) => s.name).filter(Boolean).join(", ");
  return (
    // With range buttons the chart is a group (an img may not contain controls: axe nested-interactive).
    <div className="oc-chart oc-chart-uplot" style={{ height, minHeight: height }} role={rangeSelector ? "group" : "img"} aria-label={`Time series${names ? `: ${names}` : ""}`}>
      {rangeSelector && (
        <div className="oc-chart-range" role="group" aria-label="Range">
          {[...ranges.map((r) => r.key), "MAX"].map((k) => (
            <button key={k} type="button" className="oc-chart-range-btn" aria-pressed={active === k} onClick={() => setRange(k)}>
              {k}
            </button>
          ))}
        </div>
      )}
      <Readout refEl={readout} />
      <UPlot opts={opts} data={data} height={plotH} />
    </div>
  );
}

// =================================================================== bars

type BarEvent = { points: { x: string | number; y: number | null; pointIndex: number; curveNumber: number }[] };

export interface BarChartProps {
  x?: (string | number)[];
  y?: (number | null)[];
  series?: { name: string; x: (string | number)[]; y: (number | null)[] }[];
  yFormat?: ValueFormat;
  digits?: number;
  horizontal?: boolean;
  colorBySign?: boolean;
  height?: number;
  barmode?: "group" | "stack" | "relative";
  /** Plotly layout override; only yaxis.range is read. */
  layout?: Partial<import("plotly.js").Layout>;
  onClick?: (ev: any) => void;
}

/** Vertical bars on uPlot (`horizontal` is SVG). `colorBySign` paints negatives in --signal, positives in ink. */
export function BarChart(props: BarChartProps) {
  return props.horizontal ? <HBarChart {...props} /> : <VBarChart {...props} />;
}

function barTable(x: BarChartProps["x"], y: BarChartProps["y"], series: BarChartProps["series"]) {
  const list = series ?? [{ name: "", x: x ?? [], y: y ?? [] }];
  const labels = (list[0]?.x ?? []).map(String);
  const index = new Map(labels.map((l, i) => [l, i]));
  const ys = list.map((s) => {
    const out: (number | null)[] = labels.map(() => null);
    s.x.forEach((k, i) => {
      const j = index.get(String(k));
      const v = s.y[i];
      if (j !== undefined) out[j] = typeof v === "number" && Number.isFinite(v) ? v : null;
    });
    return out;
  });
  return { labels, names: list.map((s) => s.name), ys };
}

function VBarChart({ x, y, series, yFormat = "num", digits = 2, colorBySign, height = 300, barmode = "group", layout, onClick }: BarChartProps) {
  const t = useChartTheme();
  const readout = useRef<HTMLDivElement>(null);
  const click = useRef(onClick);
  useEffect(() => {
    click.current = onClick;
  }, [onClick]);
  const table = useMemo(() => barTable(x, y, series), [x, y, series]);
  const stacked = barmode !== "group" && table.ys.length > 1;
  const data = useMemo<uPlot.AlignedData>(() => {
    const idx = table.labels.map((_, i) => i);
    if (!stacked) return [idx, ...table.ys];
    // Stacked: draw cumulative totals, the last series first, so each band shows its own share.
    const cum = table.ys.map((_, s) => table.labels.map((__, i) => table.ys.slice(0, s + 1).reduce<number>((a, ys) => a + (ys[i] ?? 0), 0)));
    return [idx, ...cum.reverse()];
  }, [table, stacked]);
  const hintKey = JSON.stringify(yHints(layout));
  const plotH = height - STRIP;
  const signed = !!colorBySign;

  const opts = useMemo<OptsBuilder>(() => {
    const h = JSON.parse(hintKey) as ReturnType<typeof yHints>;
    const n = table.labels.length;
    const k = table.ys.length;
    const order = stacked ? table.ys.map((_, i) => k - 1 - i) : table.ys.map((_, i) => i);
    const colorOf = (s: number) => (k === 1 ? t.ink : slotColor(t, s));
    const count = Math.max(2, Math.round(plotH / 56));
    const longest = Math.max(1, ...table.labels.map((l) => l.length));
    return (width: number) => {
      const rotate = n * (longest * MONO_CH + 8) > width * 0.92;
      const show = (_u: uPlot, idx: number | null | undefined) => {
        if (idx === null || idx === undefined) {
          const vals = table.ys.flat().filter((v): v is number => v !== null);
          if (!vals.length) return writeReadout(readout.current, [{ k: "NO DATA", v: "" }]);
          return writeReadout(readout.current, [
            { k: "N", v: String(n) },
            { k: "MIN", v: formatValue(Math.min(...vals), yFormat, digits, signed), signal: signed && Math.min(...vals) < 0 },
            { k: "MAX", v: formatValue(Math.max(...vals), yFormat, digits, signed), signal: signed && Math.max(...vals) < 0 },
          ]);
        }
        writeReadout(readout.current, [
          { k: table.labels[idx] ?? "", v: k === 1 ? formatValue(table.ys[0][idx], yFormat, digits, signed) : "", signal: signed && (table.ys[0][idx] ?? 0) < 0 },
          ...(k > 1 ? table.names.map((nm, s) => ({ k: nm, v: formatValue(table.ys[s][idx], yFormat, digits, signed), signal: signed && (table.ys[s][idx] ?? 0) < 0 })) : []),
        ]);
      };
      const group = 0.72;
      return {
        legend: { show: false },
        padding: [10, 8, 0, 0],
        cursor: { y: false, points: { show: false }, drag: { x: false, y: false } },
        scales: {
          x: { time: false, range: [-0.5, Math.max(0.5, n - 0.5)] },
          y: { range: (_u, min, max) => niceRange(min, max, count, [0], h.fixed) },
        },
        series: [
          {},
          ...order.map((s) => ({
            label: table.names[s],
            width: 0,
            fill: colorOf(s),
            points: { show: false },
            paths: uPlot.paths.bars!({
              size: [group, 32],
              disp: {
                ...(signed ? { fill: { unit: 3 as any, values: (u: uPlot, si: number) => (u.data[si] as (number | null)[]).map((v) => ((v ?? 0) < 0 ? t.signal : t.ink)) } } : {}),
                ...(!stacked && k > 1
                  ? {
                      x0: { unit: 1 as any, values: (u: uPlot) => Array.from(u.data[0], (i) => i - group / 2 + (s * group) / k) },
                      size: { unit: 1 as any, values: (u: uPlot) => Array.from(u.data[0], () => group / k) },
                    }
                  : {}),
              },
            }),
          })),
        ],
        axes: [
          {
            stroke: t.ink2,
            font: axisFont(t),
            gap: 4,
            grid: { show: false },
            ticks: { show: false },
            border: { show: false },
            splits: () => table.labels.map((_, i) => i),
            values: () => table.labels,
            rotate: rotate ? -90 : 0,
            size: rotate ? Math.ceil(Math.min(18, longest) * MONO_CH) + 10 : 22,
          },
          yAxis(t, yFormat, count, false),
        ],
        hooks: {
          ready: [
            (u) => {
              show(u, null);
              u.over.addEventListener("click", () => {
                const i = u.cursor.idx;
                if (i === null || i === undefined || !click.current) return;
                click.current({ points: [{ x: table.labels[i], y: table.ys[0][i], pointIndex: i, curveNumber: 0 }] } satisfies BarEvent);
              });
              if (click.current) u.over.style.cursor = "pointer";
            },
          ],
          setData: [(u) => show(u, null)],
          setCursor: [(u) => show(u, u.cursor.idx)],
          draw: [
            (u) => {
              // the zero rule, over the bars
              const { ctx, bbox } = u;
              const y = Math.round(u.valToPos(0, "y", true)) + 0.5;
              if (y < bbox.top || y > bbox.top + bbox.height) return;
              ctx.save();
              ctx.setLineDash([]);
              ctx.strokeStyle = t.ink;
              ctx.lineWidth = pxr();
              ctx.beginPath();
              ctx.moveTo(bbox.left, y);
              ctx.lineTo(bbox.left + bbox.width, y);
              ctx.stroke();
              ctx.restore();
            },
          ],
        },
      };
    };
  }, [t, table, stacked, hintKey, plotH, yFormat, digits, signed]);

  return (
    <div className="oc-chart oc-chart-uplot" style={{ height, minHeight: height }} role="img" aria-label={`Bar chart, ${table.labels.length} bars`}>
      <Readout refEl={readout} />
      <UPlot opts={opts} data={data} height={plotH} />
    </div>
  );
}

/** Horizontal bars, drawn as SVG: label column, bars from a zero rule, the value printed at the bar's end. */
function HBarChart({ x, y, series, yFormat = "num", digits = 2, colorBySign, height = 300, layout, onClick }: BarChartProps) {
  const t = useChartTheme();
  const box = useRef<HTMLDivElement>(null);
  const width = useElementWidth(box as RefObject<HTMLElement>);
  const table = useMemo(() => barTable(x, y, series), [x, y, series]);
  const fixed = yHints(layout).fixed;
  const n = table.labels.length;
  const k = table.ys.length;
  const vals = table.ys.flat().filter((v): v is number => v !== null);
  const lo = fixed ? fixed[0] : Math.min(0, ...vals);
  const hi = fixed ? fixed[1] : Math.max(0, ...vals);
  const signed = !!colorBySign;
  const fmt = (v: number | null) => formatValue(v, yFormat, digits, signed);
  const longest = Math.max(1, ...table.labels.map((l) => l.length));
  const labelW = Math.min(Math.round(width * 0.4), Math.ceil(longest * 7) + 12);
  const valueW = Math.ceil(Math.max(4, ...vals.map((v) => fmt(v).length)) * MONO_CH) + 8;
  const plotL = labelW + (lo < 0 ? valueW : 0);
  const plotR = Math.max(plotL + 20, width - valueW);
  const top = 4;
  const band = n ? (height - top - 4) / n : 0;
  const barH = Math.max(3, Math.min(14, band * 0.5));
  const X = (v: number) => plotL + ((v - lo) / (hi - lo || 1)) * (plotR - plotL);
  const zero = X(0);
  return (
    <div ref={box} className="oc-chart oc-chart-hbar" style={{ height, minHeight: height }}>
      {width > 0 && (
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Bar chart, ${n} bars`}>
          {table.labels.map((label, i) => {
            const cy = top + band * i + band / 2;
            return (
              <g key={label + i} className={onClick ? "oc-hbar-row is-clickable" : "oc-hbar-row"} onClick={onClick ? () => onClick({ points: [{ x: label, y: table.ys[0][i], pointIndex: i, curveNumber: 0 }] } satisfies BarEvent) : undefined}>
                <rect x={0} y={top + band * i} width={width} height={band} fill="transparent" />
                <text x={0} y={cy} dominantBaseline="middle" fill={t.ink} style={{ font: `12px ${t.sans}` }}>
                  {label.length * 7 > labelW - 8 ? label.slice(0, Math.max(3, Math.floor((labelW - 8) / 7) - 1)) + "…" : label}
                </text>
                {table.ys.map((ys, s) => {
                  const v = ys[i];
                  const h = barH / k;
                  const by = cy - barH / 2 + s * h;
                  if (v === null) return <text key={s} x={zero + 4} y={by + h / 2} dominantBaseline="middle" fill={t.ink2} style={{ font: `11px ${t.mono}` }}>{"—"}</text>;
                  const x0 = Math.min(zero, X(v));
                  const w = Math.max(1, Math.abs(X(v) - zero));
                  const color = signed ? (v < 0 ? t.signal : t.ink) : k === 1 ? t.ink : slotColor(t, s);
                  return (
                    <g key={s}>
                      <rect x={x0} y={by} width={w} height={Math.max(1, h - (k > 1 ? 1 : 0))} fill={color} />
                      {k === 1 && (
                        <text x={v < 0 ? x0 - 4 : x0 + w + 4} y={cy} textAnchor={v < 0 ? "end" : "start"} dominantBaseline="middle" fill={signed && v < 0 ? t.signal : t.ink} style={{ font: `11px ${t.mono}`, fontVariantNumeric: "tabular-nums" }}>
                          {fmt(v)}
                        </text>
                      )}
                    </g>
                  );
                })}
                <line x1={labelW} x2={width} y1={top + band * (i + 1) - 0.5} y2={top + band * (i + 1) - 0.5} stroke={t.ink3} strokeWidth={1} strokeDasharray="1 3" />
              </g>
            );
          })}
          <line x1={zero + 0.5} x2={zero + 0.5} y1={top} y2={height - 4} stroke={t.ink} strokeWidth={1} />
        </svg>
      )}
    </div>
  );
}

// =================================================================== histogram

export interface HistogramChartProps {
  values: (number | null)[];
  nbins?: number;
  format?: ValueFormat;
  digits?: number;
  vlines?: { x: number; label: string; color?: string }[];
  height?: number;
  normalize?: "" | "percent" | "probability" | "density";
  /** Plotly layout override; ignored. */
  layout?: Partial<import("plotly.js").Layout>;
}

/** Equal-width bins over the finite values; heights as count, percent, probability or density. */
export function histogramBins(values: (number | null)[], nbins: number, normalize: HistogramChartProps["normalize"]) {
  const v = values.filter((x): x is number => typeof x === "number" && Number.isFinite(x));
  if (!v.length) return { centers: [] as number[], heights: [] as number[], width: 0, lo: 0, hi: 0 };
  let lo = Math.min(...v);
  let hi = Math.max(...v);
  if (lo === hi) {
    lo -= 0.5 * (Math.abs(lo) || 1);
    hi += 0.5 * (Math.abs(hi) || 1);
  }
  const nb = Math.max(1, Math.round(nbins));
  const w = (hi - lo) / nb;
  const counts = new Array<number>(nb).fill(0);
  for (const x of v) counts[Math.min(nb - 1, Math.floor((x - lo) / w))] += 1;
  const scale = normalize === "probability" ? 1 / v.length : normalize === "percent" ? 100 / v.length : normalize === "density" ? 1 / (v.length * w) : 1;
  return { centers: counts.map((_, i) => lo + w * (i + 0.5)), heights: counts.map((c) => c * scale), width: w, lo, hi };
}

export function HistogramChart({ values, nbins = 60, format = "pct", digits = 2, vlines, height = 280, normalize = "probability" }: HistogramChartProps) {
  const t = useChartTheme();
  const readout = useRef<HTMLDivElement>(null);
  const bins = useMemo(() => histogramBins(values, nbins, normalize), [values, nbins, normalize]);
  const data = useMemo<uPlot.AlignedData>(() => [bins.centers, bins.heights], [bins]);
  const vKey = JSON.stringify(vlines ?? []);
  const plotH = height - STRIP;
  const yFmt: ValueFormat = normalize === "probability" ? "pct" : normalize === "percent" ? "pctPoints" : normalize === "density" ? "num" : "int";

  const opts = useMemo<OptsBuilder>(() => {
    const lines = JSON.parse(vKey) as NonNullable<HistogramChartProps["vlines"]>;
    const count = Math.max(2, Math.round(plotH / 56));
    const xs = [bins.lo, bins.hi, ...lines.map((l) => l.x)].filter(Number.isFinite);
    const xlo = Math.min(...xs) - bins.width;
    const xhi = Math.max(...xs) + bins.width;
    return (width: number) => {
      const xCount = Math.max(2, Math.round(width / 90));
      const show = (_u: uPlot, idx: number | null | undefined) => {
        if (!bins.centers.length) return writeReadout(readout.current, [{ k: "NO DATA", v: "" }]);
        if (idx === null || idx === undefined) {
          return writeReadout(readout.current, [
            { k: "BINS", v: String(bins.centers.length) },
            { k: "RANGE", v: `${formatValue(bins.lo, format, digits)} … ${formatValue(bins.hi, format, digits)}` },
            ...lines.map((l) => ({ k: l.label, v: "" })),
          ]);
        }
        const c = bins.centers[idx];
        writeReadout(readout.current, [
          { k: "BIN", v: `${formatValue(c - bins.width / 2, format, digits)} … ${formatValue(c + bins.width / 2, format, digits)}` },
          { k: normalize ? "FREQ" : "COUNT", v: formatValue(bins.heights[idx], yFmt, normalize === "density" ? 3 : 1) },
        ]);
      };
      return {
        legend: { show: false },
        padding: [16, 8, 0, 0],
        cursor: { y: false, points: { show: false }, drag: { x: false, y: false } },
        scales: {
          x: { time: false, range: [xlo, xhi] },
          y: { range: (_u, min, max) => niceRange(min, max, count, [0]) },
        },
        series: [
          {},
          {
            label: "freq",
            width: 0,
            fill: t.ink2,
            points: { show: false },
            paths: uPlot.paths.bars!({ size: [1, Infinity, 1], gap: 1 }),
          },
        ],
        axes: [
          {
            stroke: t.ink2,
            font: axisFont(t),
            gap: 4,
            size: 24,
            grid: { show: false },
            ticks: { show: true, stroke: t.ink, width: 1, size: 4 },
            border: { show: false },
            splits: (_u, _i, min, max) => ticksWithin(min, max, xCount),
            values: (_u, splits) => splits.map((v) => formatTick(v, format, splits.length > 1 ? Math.abs(splits[1] - splits[0]) : 1)),
          },
          yAxis(t, yFmt, count, false),
        ],
        hooks: {
          ready: [(u) => show(u, null)],
          setData: [(u) => show(u, null)],
          setCursor: [(u) => show(u, u.cursor.idx)],
          draw: [
            (u) => {
              xRule(u, t);
              const { ctx, bbox } = u;
              const r = pxr();
              ctx.save();
              ctx.setLineDash([]);
              ctx.font = `${Math.round(10 * r)}px ${t.mono}`;
              ctx.textBaseline = "bottom";
              for (const l of lines) {
                const x = Math.round(u.valToPos(l.x, "x", true)) + 0.5;
                const color = l.color ? resolveColor(l.color) : t.signal;
                ctx.strokeStyle = color;
                ctx.lineWidth = r;
                ctx.beginPath();
                ctx.moveTo(x, bbox.top);
                ctx.lineTo(x, bbox.top + bbox.height);
                ctx.stroke();
                ctx.fillStyle = color;
                const tw = ctx.measureText(l.label).width;
                const right = x + 4 * r + tw <= bbox.left + bbox.width;
                ctx.textAlign = right ? "left" : "right";
                ctx.fillText(l.label, right ? x + 4 * r : x - 4 * r, bbox.top - 2 * r);
              }
              ctx.restore();
            },
          ],
        },
      };
    };
  }, [t, bins, vKey, plotH, format, digits, yFmt, normalize]);

  return (
    <div className="oc-chart oc-chart-uplot" style={{ height, minHeight: height }} role="img" aria-label={`Histogram, ${bins.centers.length} bins`}>
      <Readout refEl={readout} />
      <UPlot opts={opts} data={data} height={plotH} />
    </div>
  );
}

// =================================================================== heatmap

export interface HeatmapChartProps {
  x: (string | number)[];
  y: (string | number)[];
  z: (number | null)[][];
  format?: ValueFormat;
  digits?: number;
  diverging?: boolean;
  zmid?: number;
  showValues?: boolean;
  height?: number;
  colorbar?: boolean;
  /** Plotly layout override; reads xaxis/yaxis titles, yaxis.autorange (true = first row at the bottom) and rect shapes in category coordinates. */
  layout?: Partial<import("plotly.js").Layout>;
  onClick?: (ev: any) => void;
  zmin?: number;
  zmax?: number;
  palette?: "pnl" | "neutral";
  /** Cell gap in px (default 2; 0 for dense grids). */
  gap?: number;
}

interface HeatShape {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
  color?: string;
  width?: number;
  dash?: string;
}

function heatHints(layout: unknown) {
  const L = layout as any;
  const title = (a: any) => (typeof a?.title === "string" ? a.title : typeof a?.title?.text === "string" ? a.title.text : "");
  const shapes: HeatShape[] = (Array.isArray(L?.shapes) ? L.shapes : [])
    .filter((s: any) => s?.type === "rect" && [s.x0, s.x1, s.y0, s.y1].every((v) => typeof v === "number"))
    .map((s: any) => ({ x0: s.x0, x1: s.x1, y0: s.y0, y1: s.y1, color: s.line?.color, width: s.line?.width, dash: s.line?.dash }));
  return { xTitle: title(L?.xaxis), yTitle: title(L?.yaxis), reversed: L?.yaxis?.autorange !== true, shapes };
}

function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/**
 * A canvas grid: diverging around `zmid` (palette "pnl": signal to ink; "neutral": ink density with
 * negatives back-hatched, never red; charts/heat), else the ink density ramp. Missing cells are
 * forward-hatched on bare paper, never coloured.
 */
export function HeatmapChart({ x, y, z, format = "num", digits = 2, diverging, zmid = 0, showValues, height = 360, colorbar = true, layout, onClick, zmin, zmax, palette = "pnl", gap = 2 }: HeatmapChartProps) {
  const t = useChartTheme();
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const readout = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ i: number; j: number } | null>(null);
  const width = useElementWidth(box as RefObject<HTMLElement>);
  const hints = useMemo(() => heatHints(layout), [layout]);
  const plotHeight = height - STRIP;

  const scale = useMemo(
    () => heatScale(z.flat().filter((v): v is number => typeof v === "number" && Number.isFinite(v)), { diverging, zmid, zmin, zmax, palette }, t),
    [z, diverging, zmid, zmin, zmax, palette, t],
  );

  const geom = useMemo(() => {
    const nx = x.length;
    const ny = y.length;
    const xl = x.map(String);
    const yl = y.map(String);
    const yLabW = Math.ceil(Math.max(1, ...yl.map((s) => s.length)) * MONO_CH) + 8;
    const left = yLabW + (hints.yTitle ? 16 : 0);
    const right = colorbar ? 52 : 4;
    const plotW = Math.max(10, width - left - right);
    const cellW = nx ? plotW / nx : 0;
    const xLabW = Math.max(1, ...xl.map((s) => s.length)) * MONO_CH;
    const rotate = xLabW + 6 > cellW;
    const bottom = (rotate ? Math.ceil(Math.min(14, Math.max(1, ...xl.map((s) => s.length))) * MONO_CH) + 8 : 18) + (hints.xTitle ? 16 : 0);
    const top = 4;
    const plotH = Math.max(10, plotHeight - top - bottom);
    const cellH = ny ? plotH / ny : 0;
    return { nx, ny, xl, yl, left, top, plotW, plotH, cellW, cellH, rotate, bottom };
  }, [x, y, width, plotHeight, colorbar, hints.xTitle, hints.yTitle]);

  const fmt = (v: number | null | undefined) => formatValue(v, format, digits, scale.div);
  const rowAt = (j: number) => (hints.reversed ? j : geom.ny - 1 - j); // screen row -> data row

  useEffect(() => {
    const c = canvas.current;
    if (!c || width <= 0) return;
    const r = pxr();
    c.width = Math.round(width * r);
    c.height = Math.round(plotHeight * r);
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(r, 0, 0, r, 0, 0);
    ctx.clearRect(0, 0, width, plotHeight);
    const { nx, ny, left, top, plotW, plotH, cellW, cellH, rotate, xl, yl } = geom;
    const g = Math.min(gap, cellW / 3, cellH / 3);
    ctx.font = `10px ${t.mono}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (let j = 0; j < ny; j++) {
      const row = z[rowAt(j)] ?? [];
      for (let i = 0; i < nx; i++) {
        const v = row[i];
        const cx = left + i * cellW;
        const cy = top + j * cellH;
        if (typeof v !== "number" || !Number.isFinite(v)) {
          const p = new Path2D();
          p.rect(cx + g / 2, cy + g / 2, cellW - g, cellH - g);
          hatch(ctx, p, { left: cx, top: cy, width: cellW, height: cellH }, t.ink3, 1);
          continue;
        }
        const { fill, hatch: neg } = scale.cell(v);
        ctx.fillStyle = fill;
        ctx.fillRect(cx + g / 2, cy + g / 2, cellW - g, cellH - g);
        if (neg) {
          const p = new Path2D();
          p.rect(cx + g / 2, cy + g / 2, cellW - g, cellH - g);
          backHatch(ctx, p, { left: cx, top: cy, width: cellW, height: cellH }, contrast(fill, t.ink) >= contrast(fill, t.paper) ? t.ink : t.paper);
        }
        if (showValues) {
          const text = fmt(v);
          if (text.length * 6.1 + 4 <= cellW - g && cellH - g >= 12) {
            ctx.fillStyle = contrast(fill, t.ink) >= contrast(fill, t.paper) ? t.ink : t.paper;
            ctx.fillText(text, cx + cellW / 2, cy + cellH / 2);
          }
        }
      }
    }
    // shapes (rect outlines in category coordinates)
    for (const s of hints.shapes) {
      const X = (v: number) => left + (v + 0.5) * cellW;
      const Y = (v: number) => top + (hints.reversed ? v + 0.5 : ny - 0.5 - v) * cellH;
      const x0 = Math.min(X(s.x0), X(s.x1));
      const y0 = Math.min(Y(s.y0), Y(s.y1));
      ctx.save();
      ctx.setLineDash([]);
      ctx.strokeStyle = s.color ? resolveColor(s.color) : t.ink;
      ctx.lineWidth = s.width ?? 2;
      ctx.setLineDash(s.dash === "dot" ? [2, 3] : s.dash === "dash" ? [5, 4] : []);
      ctx.strokeRect(x0, y0, Math.abs(X(s.x1) - X(s.x0)), Math.abs(Y(s.y1) - Y(s.y0)));
      ctx.restore();
    }
    // axis labels
    ctx.font = `11px ${t.mono}`;
    ctx.fillStyle = t.ink2;
    const yEvery = Math.max(1, Math.ceil(13 / Math.max(1, cellH)));
    ctx.textAlign = "right";
    for (let j = 0; j < ny; j += yEvery) ctx.fillText(yl[rowAt(j)] ?? "", left - 6, top + j * cellH + cellH / 2);
    const xEvery = Math.max(1, Math.ceil((rotate ? 13 : 0) / Math.max(1, cellW)));
    for (let i = 0; i < nx; i += xEvery) {
      const cx = left + i * cellW + cellW / 2;
      if (rotate) {
        ctx.save();
        ctx.setLineDash([]);
        ctx.translate(cx, top + plotH + 4);
        ctx.rotate(-Math.PI / 2);
        ctx.textAlign = "right";
        ctx.fillText(xl[i].length > 14 ? xl[i].slice(0, 13) + "…" : xl[i], 0, 0);
        ctx.restore();
      } else {
        ctx.textAlign = "center";
        ctx.fillText(xl[i], cx, top + plotH + 10);
      }
    }
    ctx.font = `10px ${t.mono}`;
    if (hints.xTitle) {
      ctx.textAlign = "center";
      ctx.fillText(hints.xTitle.toUpperCase(), left + plotW / 2, plotHeight - 7);
    }
    if (hints.yTitle) {
      ctx.save();
      ctx.setLineDash([]);
      ctx.translate(7, top + plotH / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.textAlign = "center";
      ctx.fillText(hints.yTitle.toUpperCase(), 0, 0);
      ctx.restore();
    }
    // colour key: stepped blocks, no gradient
    if (colorbar) {
      const kx = left + plotW + 10;
      const steps = 24;
      const bh = plotH / steps;
      for (let s = 0; s < steps; s++) {
        const v = scale.hi - ((s + 0.5) / steps) * (scale.hi - scale.lo);
        const { fill, hatch: neg } = scale.cell(v);
        ctx.fillStyle = fill;
        ctx.fillRect(kx, top + s * bh, 8, Math.ceil(bh));
        if (neg) {
          const p = new Path2D();
          p.rect(kx, top + s * bh, 8, Math.ceil(bh));
          backHatch(ctx, p, { left: kx, top: top + s * bh, width: 8, height: Math.ceil(bh) }, contrast(fill, t.ink) >= contrast(fill, t.paper) ? t.ink : t.paper);
        }
      }
      ctx.fillStyle = t.ink2;
      ctx.textAlign = "left";
      const step = Math.abs(scale.hi - scale.lo) / 4 || 1;
      ctx.fillText(formatTick(scale.hi, format, step), kx + 12, top + 5);
      if (scale.div) ctx.fillText(formatTick(zmid, format, step), kx + 12, top + plotH / 2);
      ctx.fillText(formatTick(scale.lo, format, step), kx + 12, top + plotH - 5);
    }
  }, [width, plotHeight, geom, z, scale, showValues, gap, colorbar, hints, t, format, digits, zmid]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (hover) {
      const row = rowAt(hover.j);
      writeReadout(readout.current, [
        { k: geom.yl[row] ?? "", v: "" },
        { k: geom.xl[hover.i] ?? "", v: fmt(z[row]?.[hover.i]), signal: scale.signal(z[row]?.[hover.i] ?? zmid) },
      ]);
      return;
    }
    const flat = z.flat().filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    if (!flat.length) return writeReadout(readout.current, [{ k: "NO DATA", v: "" }]);
    writeReadout(readout.current, [
      { k: "MIN", v: fmt(Math.min(...flat)), signal: scale.signal(Math.min(...flat)) },
      { k: "MAX", v: fmt(Math.max(...flat)) },
    ]);
  });

  const cellAt = (ev: React.MouseEvent) => {
    const rect = (ev.currentTarget as HTMLElement).getBoundingClientRect();
    const px = ev.clientX - rect.left - geom.left;
    const py = ev.clientY - rect.top - geom.top;
    const i = Math.floor(px / geom.cellW);
    const j = Math.floor(py / geom.cellH);
    return i >= 0 && j >= 0 && i < geom.nx && j < geom.ny ? { i, j } : null;
  };

  return (
    <div className="oc-chart oc-chart-heat" style={{ height, minHeight: height }}>
      <Readout refEl={readout} />
      <div ref={box} className="oc-heat-plot" style={{ height: plotHeight }}>
        <canvas
          ref={canvas}
          role="img"
          aria-label={`Heatmap, ${y.length} rows by ${x.length} columns`}
          style={{ width: "100%", height: plotHeight, cursor: onClick ? "pointer" : undefined }}
          onMouseMove={(ev) => {
            const c = cellAt(ev);
            setHover((h) => (c && h && c.i === h.i && c.j === h.j ? h : c));
          }}
          onMouseLeave={() => setHover(null)}
          onClick={(ev) => {
            const c = cellAt(ev);
            if (!c || !onClick) return;
            const row = rowAt(c.j);
            onClick({ points: [{ x: x[c.i], y: y[row], z: z[row]?.[c.i] ?? null, pointIndex: [row, c.i] }] });
          }}
        />
        {hover && <div className="oc-heat-hover" style={{ left: geom.left + hover.i * geom.cellW, top: geom.top + hover.j * geom.cellH, width: geom.cellW, height: geom.cellH }} />}
      </div>
    </div>
  );
}
