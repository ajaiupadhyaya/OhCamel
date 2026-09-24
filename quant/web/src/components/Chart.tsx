/**
 * Theme-aware Plotly wrapper. Plotly (~4.5 MB) is its own chunk, fetched the first time a
 * chart mounts. The template is built from the CSS tokens, so charts re-theme on toggle.
 *
 * Low level — any Plotly figure:
 *   <Chart data={traces} layout={{ yaxis: { tickformat: ".0%" } }} height={320} />
 *
 * Presets (preferred — consistent hover formats & styling):
 *   <TimeSeriesChart series={[{ name: "SPY", x, y }]} yFormat="usd" rangeSelector />
 *   <BarChart x={labels} y={values} yFormat="pct" colorBySign />
 *   <HeatmapChart x={cols} y={rows} z={matrix} format="pct" diverging />
 *   <SurfaceChart x={strikes} y={expiries} z={ivs} zFormat="pct" titles={{ x: "Strike", y: "Days", z: "IV" }} />
 *   <HistogramChart values={returns} format="pct" vlines={[{ x: -var99, label: "VaR 99%" }]} />
 *
 * Formats: "pct" (decimals shown as %), "pctPoints" (already in %), "num", "usd", "bps", "int".
 * Colours: series take categorical slots in order (--c1..--c8); series 9+ get the neutral
 * --c-other (see seriesColor in lib/theme) — hues are never repeated. Pass `color` only to encode
 * meaning (e.g. tokens.gain) — never to pick a "nicer" hue.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { Config, Data, Layout, PlotlyHTMLElement } from "plotly.js";
import { useTheme, readTokens, seriesColor, type Tokens } from "../lib/theme";
import { ChartSkeleton } from "./States";

type PlotlyModule = typeof import("plotly.js");
let plotlyPromise: Promise<PlotlyModule> | null = null;
export function loadPlotly(): Promise<PlotlyModule> {
  if (!plotlyPromise) plotlyPromise = import("plotly.js-dist-min").then((m) => ((m as any).default ?? m) as PlotlyModule);
  return plotlyPromise;
}

export type ValueFormat = "pct" | "pctPoints" | "num" | "usd" | "bps" | "int" | "x";

/** d3-format for axis ticks + hover. */
export function d3Format(f: ValueFormat | undefined, digits = 2): { tick: string; hover: string; suffix?: string; prefix?: string; tickprefix?: string; exponentformat?: "B" } {
  switch (f) {
    case "pct":
      return { tick: ".0%", hover: `.${digits}%` };
    case "pctPoints":
      return { tick: ".2f", hover: `.${digits}f`, suffix: "%" };
    case "usd":
      // Not a "$,.2~s" tickformat: d3's SI renders < $1 as "$800m" (milli) on log axes.
      // Plotly's own auto ticks with exponentformat "B" only abbreviate |exponent| ≥ 3
      // (k, M, B) — "$0.8", "$950", "$1.5k", "$3.5M" — so currency never gets milli.
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

/** The shared Plotly template (layout defaults) derived from tokens. */
export function plotlyTemplate(t: Tokens, compact = false): Partial<Layout> {
  const axis = {
    gridcolor: t.rule,
    linecolor: t.ruleStrong,
    zerolinecolor: t.ruleStrong,
    zerolinewidth: 1,
    tickcolor: t.ruleStrong,
    ticklen: 4,
    tickfont: { family: t.fontMono, size: 11, color: t.text3 },
    title: { font: { family: t.fontUi, size: 11, color: t.text3 }, standoff: 8 },
    automargin: true,
    showline: false,
    zeroline: false,
  };
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: t.fontUi, size: 12, color: t.text2 },
    colorway: t.categorical,
    margin: compact ? { l: 36, r: 8, t: 8, b: 28 } : { l: 52, r: 16, t: 12, b: 36 },
    xaxis: { ...axis, showgrid: false, showline: true, showspikes: true, spikemode: "across", spikethickness: 1, spikecolor: t.ruleStrong, spikedash: "solid", spikesnap: "cursor" } as any,
    yaxis: { ...axis, showgrid: true } as any,
    hoverlabel: { bgcolor: t.surface, bordercolor: t.ruleStrong, font: { family: t.fontMono, size: 12, color: t.text }, align: "left" },
    legend: { orientation: "h", x: 0, xanchor: "left", y: 1.02, yanchor: "bottom", font: { size: 12, color: t.text2 }, bgcolor: "rgba(0,0,0,0)", itemclick: "toggle", itemdoubleclick: "toggleothers" },
    hovermode: "closest",
    dragmode: "zoom",
    bargap: 0.25,
    modebar: { bgcolor: "rgba(0,0,0,0)", color: t.text3, activecolor: t.text },
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

/** Resolve a CSS colour expression like "var(--gain)" to a concrete value Plotly understands. */
export function resolveColor(c: string): string {
  const m = /^var\((--[\w-]+)\)$/.exec(c.trim());
  if (!m) return c;
  return getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim() || c;
}

/** "#3f66c4" / "rgb(…)" -> rgba with alpha. */
export function withAlpha(color: string, a: number): string {
  const c = resolveColor(color);
  const hex = /^#([0-9a-f]{6})$/i.exec(c);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }
  const rgb = /^rgba?\(([^)]+)\)$/.exec(c);
  if (rgb) {
    const [r, g, b] = rgb[1].split(",").map((x) => x.trim());
    return `rgba(${r}, ${g}, ${b}, ${a})`;
  }
  return c;
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
  const fullLayout = useMemo(() => {
    const user = typeof layout === "function" ? layout(tokens) : layout;
    return resolveVars(mergeLayout(mergeLayout(plotlyTemplate(tokens, compact), { height, autosize: true }), user as any));
  }, [tokens, layout, height, compact]);
  const fullData = useMemo(() => resolveVars(typeof data === "function" ? data(tokens) : data), [tokens, data]);

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

// =================================================================== presets

export interface LineSeries {
  name: string;
  x: (string | number)[];
  y: (number | null)[];
  color?: string;
  dash?: "solid" | "dot" | "dash" | "dashdot";
  width?: number;
  /** Fill to zero (area). */
  fill?: boolean;
  /** Put on a secondary row? No — never dual axes. Use two charts. */
  hoverSuffix?: string;
  customdata?: unknown[];
}

const RANGE_BUTTONS = [
  { count: 1, label: "1M", step: "month", stepmode: "backward" },
  { count: 6, label: "6M", step: "month", stepmode: "backward" },
  { count: 1, label: "YTD", step: "year", stepmode: "todate" },
  { count: 1, label: "1Y", step: "year", stepmode: "backward" },
  { count: 5, label: "5Y", step: "year", stepmode: "backward" },
  { step: "all", label: "All" },
];

/**
 * Line / area time series. One y-axis only. `rangeSelector` adds 1M…All buttons.
 * `baseline` draws a hairline at a level (e.g. 0 for returns, 1 for growth of $1).
 * With `rangeSelector` the legend sits below the plot (buttons own the top-left).
 */
export function TimeSeriesChart({ series, yFormat = "num", digits = 2, area, rangeSelector, logY, height = 320, yTitle, baseline, showLegend, compact, layout: extra }: { series: LineSeries[]; yFormat?: ValueFormat; digits?: number; area?: boolean; rangeSelector?: boolean; logY?: boolean; height?: number; yTitle?: string; baseline?: number; showLegend?: boolean; compact?: boolean; layout?: Partial<Layout> }) {
  const f = d3Format(yFormat, digits);
  const legendOn = showLegend ?? series.length > 1;
  const data = useMemo(
    () => (t: Tokens) =>
      series.map((s, i) => ({
        type: "scatter",
        mode: "lines",
        name: s.name,
        x: s.x,
        y: s.y,
        line: { width: s.width ?? (series.length > 3 ? 1.5 : 2), color: s.color ?? seriesColor(t, i), dash: s.dash, shape: "linear" },
        fill: area || s.fill ? "tozeroy" : undefined,
        fillcolor: area || s.fill ? withAlpha(s.color ?? seriesColor(t, i), series.length > 1 ? 0.06 : 0.1) : undefined,
        connectgaps: false,
        hovertemplate: `<b>%{fullData.name}</b>  ${f.prefix ?? ""}%{y:${f.hover}}${f.suffix ?? ""}${s.hoverSuffix ?? ""}<extra></extra>`,
      })) as Data[],
    [series, area, f.hover, f.prefix, f.suffix],
  );
  const layout = useMemo(
    () => (t: Tokens) =>
      mergeLayout(
        {
          hovermode: "x unified",
          showlegend: legendOn,
          xaxis: {
            type: "date",
            hoverformat: "%a %d %b %Y",
            rangeselector: rangeSelector
              ? { buttons: RANGE_BUTTONS, x: 0, y: 1.02, xanchor: "left", yanchor: "bottom", bgcolor: t.surface2, activecolor: t.surface3, bordercolor: t.rule, borderwidth: 1, font: { family: t.fontUi, size: 11, color: t.text2 } }
              : undefined,
          },
          yaxis: { tickformat: f.tick, ticksuffix: f.suffix, tickprefix: f.tickprefix ?? f.prefix, exponentformat: f.exponentformat, type: logY ? "log" : "linear", title: yTitle ? { text: yTitle } : undefined, side: "right" },
          // With range buttons along the top, the legend moves below the plot (no overlap).
          legend: rangeSelector && legendOn ? { x: 0, xanchor: "left", xref: "paper", y: 0, yanchor: "bottom", yref: "container" } : {},
          hoverlabel: { bgcolor: t.surface },
          shapes:
            baseline !== undefined
              ? [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: baseline, y1: baseline, line: { color: t.ruleStrong, width: 1, dash: "dot" } }]
              : [],
          margin: { l: 16, r: 8, t: rangeSelector || legendOn ? 36 : 12, b: rangeSelector && legendOn ? 56 : 28 },
        } as any,
        extra as any,
      ),
    [rangeSelector, logY, yTitle, baseline, f.tick, f.suffix, f.prefix, f.tickprefix, f.exponentformat, legendOn, extra],
  );
  return <Chart data={data} layout={layout} height={height} compact={compact} />;
}

/** Vertical (or horizontal) bars. `colorBySign` paints negatives in the loss colour. */
export function BarChart({ x, y, series, yFormat = "num", digits = 2, horizontal, colorBySign, height = 300, barmode = "group", layout: extra, onClick }: { x?: (string | number)[]; y?: (number | null)[]; series?: { name: string; x: (string | number)[]; y: (number | null)[] }[]; yFormat?: ValueFormat; digits?: number; horizontal?: boolean; colorBySign?: boolean; height?: number; barmode?: "group" | "stack" | "relative"; layout?: Partial<Layout>; onClick?: (ev: any) => void }) {
  const f = d3Format(yFormat, digits);
  const { resolved } = useTheme();
  const data = useMemo<Data[]>(() => {
    const t = readTokens();
    const list = series ?? [{ name: "", x: x ?? [], y: y ?? [] }];
    return list.map((s, i) => {
      const trace: Record<string, unknown> = {
        type: "bar",
        name: s.name,
        x: horizontal ? s.y : s.x,
        y: horizontal ? s.x : s.y,
        orientation: horizontal ? "h" : "v",
        hovertemplate: `${list.length > 1 ? "<b>%{fullData.name}</b> " : ""}%{${horizontal ? "y" : "x"}}: ${f.prefix ?? ""}%{${horizontal ? "x" : "y"}:${f.hover}}${f.suffix ?? ""}<extra></extra>`,
      };
      // Never pass `marker: undefined` — Plotly's cleanData does `"line" in marker` and throws.
      if (colorBySign) trace.marker = { color: s.y.map((v) => ((v ?? 0) < 0 ? t.loss : t.gain)) };
      else if (i >= 8) trace.marker = { color: seriesColor(t, i) }; // colorway would cycle; go neutral
      return trace;
    }) as Data[];
  }, [x, y, series, horizontal, colorBySign, f.hover, f.prefix, f.suffix, resolved]); // eslint-disable-line react-hooks/exhaustive-deps
  const valueAxis = { tickformat: f.tick, ticksuffix: f.suffix, tickprefix: f.tickprefix, exponentformat: f.exponentformat, zeroline: true, showgrid: true };
  const catAxis = { showgrid: false, type: "category", automargin: true, showspikes: false };
  const layout = useMemo(
    () => mergeLayout({ barmode, showlegend: (series?.length ?? 1) > 1, xaxis: horizontal ? valueAxis : catAxis, yaxis: horizontal ? { ...catAxis, autorange: "reversed" } : valueAxis, hovermode: "closest" } as any, extra as any),
    [barmode, series?.length, horizontal, extra, f.tick, f.suffix], // eslint-disable-line react-hooks/exhaustive-deps
  );
  return <Chart data={data} layout={layout} height={height} onClick={onClick} />;
}

/**
 * Heatmap. `diverging` (default when z has both signs) centres a scale at `zmid` (default 0);
 * otherwise a one-hue sequential scale. `palette`: "pnl" (loss↔gain, default — for returns)
 * or "neutral" (ochre↔cornflower — for signed quantities that are not good/bad, e.g.
 * correlations). `showValues` prints cells. `gap` = px between cells (default 2; 0 for dense grids).
 */
export function HeatmapChart({ x, y, z, format = "num", digits = 2, diverging, zmid = 0, showValues, height = 360, colorbar = true, layout: extra, onClick, zmin, zmax, palette = "pnl", gap = 2 }: { x: (string | number)[]; y: (string | number)[]; z: (number | null)[][]; format?: ValueFormat; digits?: number; diverging?: boolean; zmid?: number; showValues?: boolean; height?: number; colorbar?: boolean; layout?: Partial<Layout>; onClick?: (ev: any) => void; zmin?: number; zmax?: number; palette?: "pnl" | "neutral"; /** Cell gap in px (default 2; use 0 for dense time × asset grids). */ gap?: number }) {
  const { resolved } = useTheme();
  const f = d3Format(format, digits);
  const data = useMemo<Data[]>(() => {
    const t = readTokens();
    const flat = z.flat().filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    const div = diverging ?? (flat.some((v) => v < 0) && flat.some((v) => v > 0));
    const ramp = palette === "neutral" ? t.divergingNeutral : t.diverging;
    const scale = div ? ramp.map((c, i) => [i / (ramp.length - 1), c]) : t.sequential.map((c, i) => [i / (t.sequential.length - 1), c]);
    const m = div ? Math.max(...flat.map((v) => Math.abs(v - zmid)), 1e-12) : undefined;
    return [
      {
        type: "heatmap",
        x,
        y,
        z,
        colorscale: scale,
        zmid: div ? zmid : undefined,
        zmin: zmin ?? (div ? zmid - m! : undefined),
        zmax: zmax ?? (div ? zmid + m! : undefined),
        xgap: gap,
        ygap: gap,
        showscale: colorbar,
        colorbar: { thickness: 8, outlinewidth: 0, tickformat: f.tick, ticksuffix: f.suffix, tickprefix: f.tickprefix, exponentformat: f.exponentformat, tickfont: { family: t.fontMono, size: 10, color: t.text3 }, len: 0.9 },
        texttemplate: showValues ? `%{z:${f.hover}}${f.suffix ?? ""}` : undefined,
        textfont: { family: t.fontMono, size: 11 },
        hovertemplate: `%{y} · %{x}<br><b>${f.prefix ?? ""}%{z:${f.hover}}${f.suffix ?? ""}</b><extra></extra>`,
        hoverongaps: false,
      } as any,
    ];
  }, [x, y, z, diverging, zmid, showValues, colorbar, f.tick, f.hover, f.suffix, f.prefix, f.tickprefix, f.exponentformat, zmin, zmax, palette, gap, resolved]); // eslint-disable-line react-hooks/exhaustive-deps
  const layout = useMemo(() => mergeLayout({ xaxis: { showgrid: false, showspikes: false, type: "category", side: "bottom", showline: false }, yaxis: { showgrid: false, type: "category", autorange: "reversed", automargin: true }, margin: { l: 8, r: 8, t: 8, b: 8 } } as any, extra as any), [extra]);
  return <Chart data={data} layout={layout} height={height} onClick={onClick} />;
}

/** 3-D surface (e.g. implied vol by strike × expiry). */
export function SurfaceChart({ x, y, z, zFormat = "num", titles, height = 480, layout: extra }: { x: (number | string)[]; y: (number | string)[]; z: (number | null)[][]; zFormat?: ValueFormat; titles?: { x?: string; y?: string; z?: string }; height?: number; layout?: Partial<Layout> }) {
  const { resolved } = useTheme();
  const f = d3Format(zFormat);
  const data = useMemo<Data[]>(() => {
    const t = readTokens();
    return [
      {
        type: "surface",
        x,
        y,
        z,
        colorscale: t.sequential.map((c, i) => [i / (t.sequential.length - 1), c]),
        showscale: true,
        colorbar: { thickness: 8, outlinewidth: 0, tickformat: f.tick, tickfont: { family: t.fontMono, size: 10, color: t.text3 } },
        contours: { z: { show: true, usecolormap: true, highlightcolor: t.text, project: { z: false } } } as any,
        hovertemplate: `${titles?.x ?? "x"} %{x}<br>${titles?.y ?? "y"} %{y}<br><b>${titles?.z ?? "z"} %{z:${f.hover}}${f.suffix ?? ""}</b><extra></extra>`,
      } as any,
    ];
  }, [x, y, z, f.tick, f.hover, f.suffix, titles, resolved]); // eslint-disable-line react-hooks/exhaustive-deps
  const layout = useMemo(
    () => (t: Tokens) => {
      const ax = (title?: string, fmt?: string) => ({ title: { text: title ?? "" }, gridcolor: t.rule, zerolinecolor: t.ruleStrong, showbackground: false, tickfont: { family: t.fontMono, size: 10, color: t.text3 }, tickformat: fmt });
      return mergeLayout({ margin: { l: 0, r: 0, t: 0, b: 0 }, scene: { xaxis: ax(titles?.x), yaxis: ax(titles?.y), zaxis: ax(titles?.z, f.tick), camera: { eye: { x: 1.6, y: -1.6, z: 0.9 } }, aspectmode: "manual", aspectratio: { x: 1.2, y: 1.2, z: 0.7 } } } as any, extra as any);
    },
    [titles, f.tick, extra],
  );
  return <Chart data={data} layout={layout} height={height} />;
}

/** Distribution histogram with optional labelled vertical markers (e.g. VaR, ES). */
export function HistogramChart({ values, nbins = 60, format = "pct", digits = 2, vlines, height = 280, normalize = "probability", layout: extra }: { values: (number | null)[]; nbins?: number; format?: ValueFormat; digits?: number; vlines?: { x: number; label: string; color?: string }[]; height?: number; normalize?: "" | "percent" | "probability" | "density"; layout?: Partial<Layout> }) {
  const f = d3Format(format, digits);
  const { resolved } = useTheme();
  const data = useMemo<Data[]>(() => {
    const t = readTokens();
    return [
      {
        type: "histogram",
        x: values.filter((v) => v !== null),
        nbinsx: nbins,
        histnorm: normalize,
        marker: { color: t.categorical[0], opacity: 0.85, line: { color: t.surface, width: 1 } },
        hovertemplate: `%{x:${f.hover}}${f.suffix ?? ""}: <b>%{y:.2%}</b><extra></extra>`,
      } as any,
    ];
  }, [values, nbins, normalize, f.hover, f.suffix, resolved]); // eslint-disable-line react-hooks/exhaustive-deps
  const layout = useMemo(
    () => (t: Tokens) =>
      mergeLayout(
        {
          bargap: 0.04,
          showlegend: false,
          xaxis: { tickformat: f.tick, ticksuffix: f.suffix, tickprefix: f.tickprefix, exponentformat: f.exponentformat, showspikes: false },
          yaxis: { tickformat: normalize === "probability" ? ".0%" : undefined },
          shapes: (vlines ?? []).map((v) => ({ type: "line", yref: "paper", y0: 0, y1: 1, x0: v.x, x1: v.x, line: { color: v.color ?? t.loss, width: 1.5, dash: "dot" } })),
          annotations: (vlines ?? []).map((v) => ({ x: v.x, yref: "paper", y: 1, text: v.label, showarrow: false, xanchor: "left", yanchor: "top", xshift: 4, font: { size: 11, color: t.text2 } })),
        } as any,
        extra as any,
      ),
    [vlines, f.tick, f.suffix, f.tickprefix, f.exponentformat, normalize, extra],
  );
  return <Chart data={data} layout={layout} height={height} />;
}
