/**
 * Charts. The four presets are uPlot/canvas (src/charts/UPlot.tsx) and never load Plotly:
 *   <TimeSeriesChart series={[{ name: "SPY", x, y }]} yFormat="usd" rangeSelector />
 *   <BarChart x={labels} y={values} yFormat="pct" colorBySign />
 *   <HeatmapChart x={cols} y={rows} z={matrix} format="pct" diverging />
 *   <HistogramChart values={returns} format="pct" vlines={[{ x: -var99, label: "VaR 99%" }]} />
 *
 * Plotly (~4.5 MB) stays only for the raw <Chart> on pages Lane P2 has not migrated yet
 * (scripts/check-bundle.mjs LEGACY_PLOTLY: Rates & Macro, Company, Ticker, Engine) and for the
 * 3-D surface (pages/volatility/SurfaceView.tsx, which loads it itself). It reaches Plotly only
 * through loadPlotly()'s dynamic import() of ./plotlyLegacy (a chunk check-bundle can name), so
 * a page pays for it only when a raw <Chart> mounts. Delete it with the last user. Its template is paper tape: ink lines at 1.25px, dotted --ink-3 grid, Plex
 * Mono 11px ticks, transparent backgrounds, and no legend box -- line traces get direct
 * end-of-line labels instead.
 *
 * Formats: "pct" (decimals shown as %), "pctPoints" (already in %), "num", "usd", "bps", "int", "x".
 * Colours: series take the slots --c1..--c8 in order (ink, ink-2, signal, ink-3, earths), then
 * the neutral --c-other. Pass `color` only to encode meaning (a loss is var(--loss)).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { Config, Data, Layout, PlotlyHTMLElement } from "plotly.js";
import { useTheme, readTokens, type Tokens } from "../lib/theme";
import { endLabels, type ValueFormat } from "../charts/scales";
import { resolveColor, withAlpha } from "../charts/theme";
import { ChartSkeleton } from "./States";

export { TimeSeriesChart, BarChart, HeatmapChart, HistogramChart, type LineSeries } from "../charts/UPlot";
export { resolveColor, withAlpha };
export type { ValueFormat };

type PlotlyModule = typeof import("plotly.js");
let plotlyPromise: Promise<PlotlyModule> | null = null;
/** The only path to Plotly: a dynamic import, so it never lands in the entry chunk. */
export function loadPlotly(): Promise<PlotlyModule> {
  if (!plotlyPromise) plotlyPromise = import("./plotlyLegacy").then((m) => { const p = m.default as any; return (p.default ?? p) as PlotlyModule; });
  return plotlyPromise;
}

/** d3-format for Plotly axis ticks + hover. */
export function d3Format(f: ValueFormat | undefined, digits = 2): { tick: string; hover: string; suffix?: string; prefix?: string; tickprefix?: string; exponentformat?: "B" } {
  switch (f) {
    case "pct":
      return { tick: ".0%", hover: `.${digits}%` };
    case "pctPoints":
      return { tick: ".2f", hover: `.${digits}f`, suffix: "%" };
    case "usd":
      // Not a "$,.2~s" tickformat: d3's SI renders < $1 as "$800m" (milli) on log axes.
      return { tick: "", hover: `$,.${digits}f`, tickprefix: "$", exponentformat: "B" };
    case "bps":
      return { tick: ",.0f", hover: ",.0f", suffix: " bp" };
    case "int":
      return { tick: ",.0f", hover: ",.0f" };
    case "x":
      return { tick: ".2f", hover: `.${digits}f`, suffix: "×" };
    default:
      return { tick: ",.4~g", hover: `,.${digits}f` };
  }
}

/** The paper tape Plotly template (layout defaults), built from the live tokens. */
export function plotlyTemplate(t: Tokens, compact = false): Partial<Layout> {
  const axis = {
    gridcolor: t.rule, // --ink-3
    griddash: "dot",
    gridwidth: 1,
    linecolor: t.text,
    linewidth: 1,
    zerolinecolor: t.text,
    zerolinewidth: 1,
    tickcolor: t.text,
    ticklen: 4,
    tickfont: { family: t.fontMono, size: 11, color: t.text2 },
    title: { font: { family: t.fontMono, size: 11, color: t.text2 }, standoff: 8 },
    automargin: true,
    showline: false,
    zeroline: false,
  };
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: t.fontMono, size: 11, color: t.text2 },
    colorway: t.categorical,
    margin: compact ? { l: 36, r: 8, t: 8, b: 28 } : { l: 52, r: 16, t: 12, b: 36 },
    xaxis: { ...axis, showgrid: false, showline: true, ticks: "outside", showspikes: true, spikemode: "across", spikethickness: 1, spikecolor: t.text2, spikedash: "dot", spikesnap: "cursor" } as any,
    yaxis: { ...axis, showgrid: true, ticks: "" } as any,
    hoverlabel: { bgcolor: t.bg, bordercolor: t.text, font: { family: t.fontMono, size: 11, color: t.text }, align: "left" },
    showlegend: false,
    legend: { orientation: "h", x: 0, xanchor: "left", y: 1.02, yanchor: "bottom", font: { family: t.fontMono, size: 11, color: t.text2 }, bgcolor: "rgba(0,0,0,0)", borderwidth: 0, itemclick: "toggle", itemdoubleclick: "toggleothers" },
    hovermode: "closest",
    dragmode: "zoom",
    bargap: 0.25,
    modebar: { bgcolor: "rgba(0,0,0,0)", color: t.text2, activecolor: t.text },
  } as Partial<Layout>;
}

function isObj(x: unknown): x is Record<string, any> {
  return !!x && typeof x === "object" && !Array.isArray(x);
}
/** Deep merge for layout objects (arrays replaced). */
export function mergeLayout<T extends Record<string, any>>(base: T, over: Record<string, any> | undefined): T {
  if (!over) return base;
  const out: Record<string, any> = { ...base };
  for (const [k, v] of Object.entries(over)) out[k] = isObj(v) && isObj(out[k]) ? mergeLayout(out[k], v) : v;
  return out as T;
}

/** Deep-replace "var(--token)" strings (skips numeric arrays) so authors can use tokens in traces. */
function resolveVars<T>(x: T): T {
  if (typeof x === "string") return (x.startsWith("var(") ? resolveColor(x) : x) as T;
  if (Array.isArray(x)) return (x.length && typeof x[0] !== "string" && typeof x[0] !== "object" ? x : x.map(resolveVars)) as T;
  if (x && typeof x === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(x)) out[k] = resolveVars(v);
    return out as T;
  }
  return x;
}

const isLineTrace = (d: any) => (d.type === undefined || d.type === "scatter" || d.type === "scattergl") && (d.mode === undefined || String(d.mode).includes("lines"));

/** Ink lines at 1.25px unless the trace sets its own width. */
function traceDefaults(data: Data[]): Data[] {
  return data.map((d: any) => (isLineTrace(d) && d.line?.width === undefined ? { ...d, line: { ...(d.line ?? {}), width: 1.25 } } : d));
}

/**
 * No legend box. Where a legend would show (asked for, or several named traces), line traces
 * get direct end-of-line labels as annotations instead; charts that are not all lines keep a
 * boxless legend because their marks cannot carry a label at a line end.
 */
function directLabels(data: Data[], layout: Record<string, any>, t: Tokens, height: number): Record<string, any> {
  const named = (data as any[]).filter((d) => d.name && d.showlegend !== false && d.visible !== false && d.visible !== "legendonly");
  const wants = layout.showlegend === true || (layout.showlegend === undefined && named.length > 1);
  if (!wants || !named.length) return { ...layout, showlegend: false };
  if (!named.every(isLineTrace)) return { ...layout, showlegend: true };
  const items: { y: number; text: string; x: unknown; color: string; xref: string; yref: string }[] = [];
  (data as any[]).forEach((d, i) => {
    if (!named.includes(d) || d.line?.width === 0 || !Array.isArray(d.x) || !Array.isArray(d.y)) return;
    for (let k = d.y.length - 1; k >= 0; k--) {
      const y = d.y[k];
      if (typeof y === "number" && Number.isFinite(y) && d.x[k] !== undefined && d.x[k] !== null) {
        const color = d.line?.color ?? t.categorical[i % t.categorical.length];
        items.push({ y, text: String(i), x: d.x[k], color: color === t.rule ? t.text2 : color, xref: d.xaxis ?? "x", yref: d.yaxis ?? "y" });
        break;
      }
    }
  });
  if (!items.length) return { ...layout, showlegend: false };
  const log = (layout.yaxis?.type ?? "") === "log";
  // collision gap in data units: 13px of the axis extent the traces span
  let lo = Infinity;
  let hi = -Infinity;
  for (const d of named as any[]) {
    for (const v of Array.isArray(d.y) ? d.y : []) {
      if (typeof v !== "number" || !Number.isFinite(v)) continue;
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }
  }
  const span = hi - lo || 1;
  const placed = log ? items : endLabels(items, (span * 13) / Math.max(120, height));
  const byIdx = new Map(placed.map((p) => [p.text, p.y]));
  const names = new Map((data as any[]).map((d, i) => [String(i), String(d.name)]));
  const longest = Math.max(...items.map((it) => (names.get(it.text) ?? "").length));
  // annotations on a log axis are placed in log10 units
  const axisOf = (ref: string, kind: "x" | "y") => layout[`${kind}axis${ref.slice(1)}`] ?? {};
  const onAxis = (v: unknown, ref: string, kind: "x" | "y") => (axisOf(ref, kind).type === "log" && typeof v === "number" && v > 0 ? Math.log10(v) : v);
  const annotations = items.map((it) => ({
    x: onAxis(it.x, it.xref, "x"),
    y: onAxis(byIdx.get(it.text) ?? it.y, it.yref, "y"),
    xref: it.xref,
    yref: it.yref,
    text: names.get(it.text),
    showarrow: false,
    xanchor: "left",
    xshift: 6,
    font: { family: t.fontMono, size: 11, color: it.color },
  }));
  return {
    ...layout,
    showlegend: false,
    annotations: [...(layout.annotations ?? []), ...annotations],
    margin: { ...(layout.margin ?? {}), r: Math.max(layout.margin?.r ?? 0, Math.min(160, Math.ceil(longest * 6.7) + 14)) },
  };
}

export interface ChartProps {
  /** Traces, or a function of the current theme tokens (re-run on theme toggle). "var(--token)" colours are resolved. */
  data: Data[] | ((t: Tokens) => Data[]);
  layout?: Partial<Layout> | ((t: Tokens) => Partial<Layout>);
  config?: Partial<Config>;
  height?: number;
  className?: string;
  compact?: boolean;
  onClick?: (ev: any) => void;
  ariaLabel?: string;
}

/** Raw Plotly figure in the paper tape template. Prefer the uPlot presets; this loads Plotly. */
export function Chart({ data, layout, config, height = 300, className, compact, onClick, ariaLabel }: ChartProps) {
  const el = useRef<HTMLDivElement>(null);
  const { resolved } = useTheme();
  const [plotly, setPlotly] = useState<PlotlyModule | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    loadPlotly().then((p) => live && setPlotly(p), (e) => live && setErr(String(e)));
    return () => {
      live = false;
    };
  }, []);

  // tokens re-read whenever the theme flips
  const tokens = useMemo(() => readTokens(), [resolved]); // eslint-disable-line react-hooks/exhaustive-deps
  const fullData = useMemo(() => traceDefaults(resolveVars(typeof data === "function" ? data(tokens) : data)), [tokens, data]);
  const fullLayout = useMemo(() => {
    const user = typeof layout === "function" ? layout(tokens) : layout;
    const merged = resolveVars(mergeLayout(mergeLayout(plotlyTemplate(tokens, compact), { height, autosize: true }), user as any));
    // the template's showlegend:false is a default, not the caller's choice
    const asked = (user as any)?.showlegend;
    return directLabels(fullData, { ...merged, showlegend: asked }, tokens, height);
  }, [tokens, layout, height, compact, fullData]);

  useEffect(() => {
    if (!plotly || !el.current) return;
    const cfg: Partial<Config> = { displaylogo: false, responsive: true, displayModeBar: "hover" as any, modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines", "hoverClosestCartesian", "hoverCompareCartesian"] as any, ...config };
    plotly.react(el.current, fullData, fullLayout as Layout, cfg);
  }, [plotly, fullData, fullLayout, config]);

  useEffect(() => {
    const node = el.current as (HTMLDivElement & Partial<PlotlyHTMLElement>) | null;
    if (!plotly || !node || !onClick || !node.on) return;
    node.on("plotly_click", onClick);
    return () => {
      (node as any).removeAllListeners?.("plotly_click");
    };
  }, [plotly, onClick]);

  useEffect(() => {
    if (!plotly || !el.current) return;
    const node = el.current;
    const ro = new ResizeObserver(() => plotly.Plots.resize(node));
    ro.observe(node);
    return () => ro.disconnect();
  }, [plotly]);

  useEffect(() => {
    const node = el.current;
    return () => {
      if (node && plotly) plotly.purge(node);
    };
  }, [plotly]);

  if (err) return <div className="oc-chart-error subtle small">Chart library failed to load: {err}</div>;
  return (
    <div className={`oc-chart ${className ?? ""}`} style={{ height, minHeight: height }} role="img" aria-label={ariaLabel}>
      {!plotly && <ChartSkeleton height={height} />}
      <div ref={el} style={{ width: "100%", height: plotly ? height : 0 }} />
    </div>
  );
}
