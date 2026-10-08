/**
 * The 3-D implied-vol surface: the one Plotly view in the app (Ship plan P2-5). This module is
 * the only importer of plotly.js-dist-min and is itself loaded lazily (React.lazy in
 * ImpliedTab), so the ~4.5 MB chunk is fetched only when the surface is on screen.
 * scripts/check-bundle.mjs fails the build if any other chunk imports Plotly.
 *
 * Paper Tape in 3-D: the ink density ramp, mono 10px ticks, hairline grid, no background
 * panes, a thin colour bar; drag to rotate.
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import type { Data, Layout, PlotlyHTMLElement } from "plotly.js";
import { ChartSkeleton } from "../../components/States";
import { readTokens, useTheme } from "../../lib/theme";

type PlotlyModule = typeof import("plotly.js");
let plotly: Promise<PlotlyModule> | null = null;
const loadPlotly = () => (plotly ??= import("plotly.js-dist-min").then((m) => ((m as { default?: unknown }).default ?? m) as PlotlyModule));

export interface SurfaceViewProps {
  /** log-moneyness k = ln(K/F) */
  k: number[];
  /** calendar days to expiry */
  days: number[];
  /** implied vol, rows by days, columns by k (decimals; null where not interpolated) */
  iv: (number | null)[][];
  height?: number;
}

export default function SurfaceView({ k, days, iv, height = 520 }: SurfaceViewProps) {
  const el = useRef<HTMLDivElement>(null);
  const { resolved } = useTheme();
  const [lib, setLib] = useState<PlotlyModule | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    loadPlotly().then(
      (p) => live && setLib(p),
      (e) => live && setErr(String(e)),
    );
    return () => {
      live = false;
    };
  }, []);

  const fig = useMemo(() => {
    const t = readTokens();
    const axis = (title: string, tickformat?: string) => ({
      title: { text: title, font: { family: t.fontMono, size: 10, color: t.text2 } },
      gridcolor: t.rule,
      zerolinecolor: t.ruleStrong,
      showbackground: false,
      tickfont: { family: t.fontMono, size: 10, color: t.text3 },
      tickformat,
    });
    const data = [
      {
        type: "surface",
        x: k,
        y: days,
        z: iv,
        colorscale: t.sequential.map((c, i) => [i / (t.sequential.length - 1), c]),
        showscale: true,
        colorbar: { thickness: 6, outlinewidth: 0, tickformat: ".0%", tickfont: { family: t.fontMono, size: 10, color: t.text3 } },
        contours: { z: { show: true, usecolormap: true, highlightcolor: t.text, project: { z: false } } },
        hovertemplate: "K %{x:+.3f}<br>%{y:.0f}D<br><b>IV %{z:.2%}</b><extra></extra>",
      },
    ] as unknown as Data[];
    const layout = {
      height,
      autosize: true,
      margin: { l: 0, r: 0, t: 0, b: 0 },
      paper_bgcolor: "rgba(0,0,0,0)",
      font: { family: t.fontMono, size: 10, color: t.text2 },
      hoverlabel: { bgcolor: t.bg, bordercolor: t.text, font: { family: t.fontMono, size: 11, color: t.text } },
      scene: {
        xaxis: axis("K = LN(K/F)", "+.2f"),
        yaxis: axis("DAYS"),
        zaxis: axis("IV", ".0%"),
        camera: { eye: { x: 1.35, y: -1.45, z: 0.7 } },
        aspectmode: "manual",
        aspectratio: { x: 1.3, y: 1.3, z: 0.75 },
      },
    } as unknown as Partial<Layout>;
    return { data, layout };
  }, [k, days, iv, height, resolved]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!lib || !el.current) return;
    void lib.react(el.current, fig.data, fig.layout as Layout, { displaylogo: false, responsive: true, displayModeBar: false });
  }, [lib, fig]);

  useEffect(() => {
    if (!lib || !el.current) return;
    const node = el.current;
    const ro = new ResizeObserver(() => lib.Plots.resize(node));
    ro.observe(node);
    return () => {
      ro.disconnect();
      lib.purge(node as unknown as PlotlyHTMLElement);
    };
  }, [lib]);

  if (err) return <div className="vx-surface-err num">PLOTLY UNAVAILABLE · {err}</div>;
  return (
    <div className="vx-surface" style={{ "--chart-h": `${height}px` } as CSSProperties} role="img" aria-label="Implied volatility surface over log-moneyness and days to expiry">
      {!lib && <ChartSkeleton height={height} />}
      <div ref={el} className={lib ? "vx-surface-plot" : "vx-surface-plot vx-surface-pending"} />
    </div>
  );
}
