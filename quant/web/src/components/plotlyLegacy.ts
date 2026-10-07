/**
 * The legacy raw <Chart>'s door to Plotly (components/Chart.tsx loads this module with a
 * dynamic import()). It exists so scripts/check-bundle.mjs can name this chunk: while
 * LEGACY_PLOTLY lists a page still rendering a raw <Chart>, this chunk may import Plotly;
 * once the list is empty, this file and the raw <Chart> are deleted and only the surface
 * view (pages/volatility/SurfaceView.tsx) imports Plotly.
 */
import * as Plotly from "plotly.js-dist-min";

export default Plotly;
