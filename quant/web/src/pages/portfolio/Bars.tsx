/**
 * Grouped / single-series bars for the Portfolio Lab.
 *
 * Kept (rather than the shared <BarChart/>, which is now fixed) for its extras: per-bar
 * `colors`, token-function series, and custom margins.
 */
import { useMemo } from "react";
import type { Data } from "plotly.js";
import { Chart, d3Format, type ValueFormat } from "../../components";
import type { Tokens } from "../../lib/theme";

export interface BarSeries {
  name: string;
  x: (string | number)[];
  y: (number | null)[];
  /** Meaningful colour (token or "var(--…)"); default = categorical slot. */
  color?: string;
  /** Per-bar colours (e.g. to encode sign). */
  colors?: string[];
}

export function Bars({ series, yFormat = "pct", digits = 2, horizontal, height = 300, showLegend, margin }: { series: BarSeries[] | ((t: Tokens) => BarSeries[]); yFormat?: ValueFormat; digits?: number; horizontal?: boolean; height?: number; showLegend?: boolean; margin?: { l?: number; r?: number; t?: number; b?: number } }) {
  const f = d3Format(yFormat, digits);
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const list = typeof series === "function" ? series(t) : series;
      return list.map(
        (s, i) =>
          ({
            type: "bar",
            name: s.name,
            orientation: horizontal ? "h" : "v",
            x: horizontal ? s.y : s.x,
            y: horizontal ? s.x : s.y,
            marker: { color: s.colors ?? s.color ?? t.categorical[i % 8], line: { width: 0 } },
            hovertemplate: `${list.length > 1 ? "<b>%{fullData.name}</b> " : ""}%{${horizontal ? "y" : "x"}}: ${f.prefix ?? ""}%{${horizontal ? "x" : "y"}:${f.hover}}${f.suffix ?? ""}<extra></extra>`,
          }) as Data,
      );
    },
    [series, horizontal, f.hover, f.prefix, f.suffix],
  );
  const n = typeof series === "function" ? 2 : series.length;
  const layout = useMemo(() => {
    const valueAxis = { tickformat: f.tick, ticksuffix: f.suffix, zeroline: true, showgrid: true, showspikes: false };
    const catAxis = { showgrid: false, type: "category", automargin: true, showspikes: false };
    return {
      barmode: "group",
      bargap: 0.28,
      bargroupgap: 0.08,
      hovermode: "closest",
      showlegend: showLegend ?? n > 1,
      margin: { l: 8, r: 16, t: (showLegend ?? n > 1) ? 30 : 8, b: 28, ...margin },
      xaxis: horizontal ? valueAxis : catAxis,
      yaxis: horizontal ? { ...catAxis, autorange: "reversed" } : { ...valueAxis, side: "right" },
    };
  }, [horizontal, f.tick, f.suffix, showLegend, n, margin]);
  return <Chart data={data} layout={layout as any} height={height} />;
}
