/**
 * Charts. The four presets are uPlot/canvas (src/charts/UPlot.tsx) and never load Plotly:
 *   <TimeSeriesChart series={[{ name: "SPY", x, y }]} yFormat="usd" rangeSelector />
 *   <BarChart x={labels} y={values} yFormat="pct" colorBySign />
 *   <HeatmapChart x={cols} y={rows} z={matrix} format="pct" diverging />
 *   <HistogramChart values={returns} format="pct" vlines={[{ x: -var99, label: "VaR 99%" }]} />
 * Shapes the presets do not cover: src/charts/XYChart.tsx (lines, points, bars on one axis).
 *
 * The raw Plotly Chart component is gone (Ship plan P2-7 migrated its last page, the Engine). Plotly
 * is the 3-D surface view's alone (pages/volatility/SurfaceView.tsx, lazy); scripts/check-bundle.mjs
 * fails the build on any other importer.
 *
 * Formats: "pct" (decimals shown as %), "pctPoints" (already in %), "num", "usd", "bps", "int", "x".
 */
import type { ValueFormat } from "../charts/scales";
import { resolveColor, withAlpha } from "../charts/theme";

export { TimeSeriesChart, BarChart, HeatmapChart, HistogramChart, type LineSeries } from "../charts/UPlot";
export { resolveColor, withAlpha };
export type { ValueFormat };
