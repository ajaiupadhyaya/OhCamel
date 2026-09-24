/**
 * Small building blocks shared by the Strategy Lab tabs: ticker availability, the
 * editorial verdict block, a pre-binned histogram and number helpers.
 */
import { useMemo, type ReactNode } from "react";
import type { Data } from "plotly.js";
import { Chart, InfoTip, type InfoProp } from "../../components";
import { useApiQuery } from "../../lib/query";
import type { Tokens } from "../../lib/theme";

// ------------------------------------------------------------------ availability

interface OverviewLite {
  as_of?: string | null;
  rows: { ticker: string; error?: string | null; as_of?: string | null }[];
}

export type Availability = {
  status: (t: string) => "ok" | "missing" | "pending";
  reason: (t: string) => string | undefined;
  loading: boolean;
  asOf: string | null;
};

/**
 * Which tickers any configured data source can serve, via GET /market/overview (cheap,
 * cached). Offline, only the committed fixtures are available; online, almost everything.
 * If the check itself fails, tickers are treated as available and the backtest reports
 * any real problem.
 */
export function useAvailability(tickers: string[]): Availability {
  const list = useMemo(
    () => [...new Set(tickers.map((t) => t.toUpperCase()))].sort(),
    [tickers],
  );
  const q = useApiQuery<OverviewLite>(
    "/market/overview",
    { tickers: list.join(",") },
    { enabled: list.length > 0, staleTime: 30 * 60_000 },
  );
  return useMemo(() => {
    const rows = new Map(
      (q.data?.rows ?? []).map((r) => [r.ticker.toUpperCase(), r]),
    );
    const failed = q.isError;
    return {
      status: (t: string) => {
        if (failed) return "ok";
        const r = rows.get(t.toUpperCase());
        if (!r) return "pending";
        return r.error ? "missing" : "ok";
      },
      reason: (t: string) => rows.get(t.toUpperCase())?.error ?? undefined,
      loading: q.isLoading || (q.isFetching && list.some((t) => !rows.has(t))),
      asOf:
        q.data?.as_of ??
        (q.data?.rows
          .map((r) => r.as_of)
          .filter(Boolean)
          .sort()
          .pop() as string | undefined) ??
        null,
    };
  }, [q.data, q.isError, q.isLoading, q.isFetching, list]);
}

// ------------------------------------------------------------------ verdict

export function Verdict({
  eyebrow,
  head,
  children,
  stats,
  tone,
}: {
  eyebrow?: ReactNode;
  head: ReactNode;
  children?: ReactNode;
  stats?: {
    label: ReactNode;
    value: ReactNode;
    caption?: ReactNode;
    info?: InfoProp;
    tone?: string;
  }[];
  tone?: "gain" | "loss" | "warn" | "neutral";
}) {
  return (
    <div className={`sl-verdict sl-tone-${tone ?? "neutral"}`}>
      {eyebrow && <div className="eyebrow">{eyebrow}</div>}
      <h2 className="sl-verdict-head display">{head}</h2>
      {stats && stats.length > 0 && (
        <div className="sl-verdict-grid">
          {stats.map((s, i) => (
            <div className="sl-verdict-stat" key={i}>
              <div className="sl-verdict-label">
                {s.label}
                <InfoTip
                  info={s.info}
                  size={12}
                  label={typeof s.label === "string" ? s.label : undefined}
                />
              </div>
              <div className={`sl-verdict-value num ${s.tone ?? ""}`}>
                {s.value}
              </div>
              {s.caption && (
                <div className="sl-verdict-caption subtle small">
                  {s.caption}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {children && <div className="sl-verdict-text">{children}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ binned histogram

/**
 * Histogram from server-side bins (edges length = counts length + 1). Bars are coloured by
 * which side of `split` their centre falls on (loss colour left, gain right) when `split`
 * is given; `vlines` are labelled markers.
 */
export function BinnedHistogram({
  counts,
  edges,
  split,
  vlines,
  xTitle,
  xFormat = ".2f",
  height = 260,
  normalize = true,
}: {
  counts: number[];
  edges: number[];
  split?: number;
  vlines?: { x: number; label: string; color?: string; dash?: string }[];
  xTitle?: string;
  xFormat?: string;
  height?: number;
  normalize?: boolean;
}) {
  const total = counts.reduce((a, b) => a + b, 0) || 1;
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => {
        const centers = counts.map((_, i) => (edges[i] + edges[i + 1]) / 2);
        const widths = counts.map((_, i) => edges[i + 1] - edges[i]);
        const y = counts.map((c) => (normalize ? c / total : c));
        const colors = centers.map((c) =>
          split === undefined ? t.categorical[0] : c <= split ? t.loss : t.gain,
        );
        return [
          {
            type: "bar",
            x: centers,
            y,
            width: widths,
            marker: {
              color: colors,
              opacity: 0.8,
              line: { color: t.surface, width: 1 },
            },
            customdata: counts.map((_, i) => [
              edges[i],
              edges[i + 1],
              counts[i],
            ]),
            hovertemplate: `%{customdata[0]:${xFormat}} to %{customdata[1]:${xFormat}}<br><b>${normalize ? "%{y:.1%}" : "%{y}"}</b> (%{customdata[2]:,})<extra></extra>`,
          } as Data,
        ];
      },
    [counts, edges, split, normalize, total, xFormat],
  );
  const layout = useMemo(
    () => (t: Tokens) => ({
      bargap: 0,
      showlegend: false,
      margin: { l: 44, r: 12, t: 26, b: 40 },
      xaxis: {
        title: xTitle ? { text: xTitle } : undefined,
        tickformat: xFormat,
        showspikes: false,
        zeroline: false,
      },
      yaxis: { tickformat: normalize ? ".0%" : ",d", rangemode: "tozero" },
      shapes: (vlines ?? []).map((v) => ({
        type: "line",
        yref: "paper",
        y0: 0,
        y1: 1,
        x0: v.x,
        x1: v.x,
        line: { color: v.color ?? t.text2, width: 1.5, dash: v.dash ?? "dot" },
      })),
      annotations: (vlines ?? []).map((v, i) => ({
        x: v.x,
        yref: "paper",
        y: 1,
        yanchor: "bottom",
        text: v.label,
        showarrow: false,
        xanchor: i % 2 ? "right" : "left",
        xshift: i % 2 ? -4 : 4,
        font: { size: 11, color: t.text2, family: t.fontUi },
      })),
    }),
    [vlines, xTitle, xFormat, normalize],
  );
  return <Chart data={data} layout={layout as any} height={height} />;
}

// ------------------------------------------------------------------ helpers

export const finite = (x: number | null | undefined): x is number =>
  typeof x === "number" && Number.isFinite(x);

/** "0.49" style Sharpe. */
export const fmtSR = (x: number | null | undefined, d = 2) =>
  finite(x) ? x.toFixed(d).replace("-", "−") : "—";

/** Probability as a percent with sensible precision near the ends. */
export function fmtProb(p: number | null | undefined): string {
  if (!finite(p)) return "—";
  if (p > 0.999) return ">99.9%";
  if (p < 0.001) return "<0.1%";
  return `${(p * 100).toFixed(p > 0.99 || p < 0.01 ? 1 : 0)}%`;
}

export function fmtYears(y: number | null | undefined): string {
  if (y === Infinity) return "∞";
  if (!finite(y)) return "—";
  return y >= 100 ? `${Math.round(y)} yrs` : `${y.toFixed(1)} yrs`;
}

/** Converts a list of values to category labels for heatmaps. */
export const label = (v: number) => String(+v.toFixed(6));

// ------------------------------------------------------------------ bars

/**
 * Grouped bars built on <Chart> (page-local styling; the shared BarChart also works now).
 */
export function Bars({
  series,
  tick = ".2f",
  hover = ".2f",
  suffix = "",
  dateX,
  height = 280,
  colorBySign,
  layout: extra,
}: {
  series: {
    name: string;
    x: (string | number)[];
    y: (number | null)[];
    color?: string;
  }[];
  tick?: string;
  hover?: string;
  suffix?: string;
  dateX?: boolean;
  height?: number;
  colorBySign?: boolean;
  layout?: Record<string, unknown>;
}) {
  const data = useMemo(
    () =>
      (t: Tokens): Data[] =>
        series.map((s, i) => ({
          type: "bar",
          name: s.name,
          x: s.x,
          y: s.y,
          marker: {
            color: colorBySign
              ? s.y.map((v) => ((v ?? 0) < 0 ? t.loss : t.gain))
              : (s.color ?? t.categorical[i % 8]),
          },
          hovertemplate: `${series.length > 1 ? "<b>%{fullData.name}</b> " : ""}%{x${dateX ? "|%b %Y" : ""}}: %{y:${hover}}${suffix}<extra></extra>`,
        })) as Data[],
    [series, hover, suffix, dateX, colorBySign],
  );
  const layout = useMemo(
    () => ({
      barmode: "group",
      bargap: dateX ? 0.15 : 0.25,
      showlegend: series.length > 1,
      hovermode: "closest",
      xaxis: dateX
        ? { type: "date", showspikes: false }
        : { type: "category", showgrid: false, showspikes: false },
      yaxis: { tickformat: tick, ticksuffix: suffix, zeroline: true },
      margin: { l: 48, r: 8, t: series.length > 1 ? 30 : 10, b: 30 },
      ...(extra ?? {}),
    }),
    [dateX, tick, suffix, series.length, extra],
  );
  return <Chart data={data} layout={layout as any} height={height} />;
}
