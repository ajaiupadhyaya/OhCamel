/**
 * Curve history — GET /api/macro/curve/history?years=&pca_years=: month-end yields heatmap,
 * 2s10s / 3m10y / 5s30s (+ butterfly) with inversions shaded, and a PCA of daily yield
 * changes (level / slope / curvature loadings, explained variance, cumulative factor levels).
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart, Panel, SegmentedControl, StatGrid, StatTile, withAlpha } from "../../components";
import { fmtBps, fmtDate, fmtPct } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import { INFO } from "./info";
import { Bars, Controls, MethodCard, Swatch, ordinal, runs, tenorAxis, tenorLabel } from "./shared";
import type { CurveHistory } from "./types";

const YEARS = ["5", "10", "20", "30"] as const;
const PCA_YEARS = ["5", "10", "20"] as const;
const SPREAD_INFO: Record<string, { label: string; info: typeof INFO.s2s10 }> = {
  "2s10s": { label: "2s10s", info: INFO.s2s10 },
  "3m10y": { label: "3m10y", info: INFO.s3m10y },
  "5s30s": { label: "5s30s", info: INFO.s5s30 },
  "2s5s10s": { label: "2s5s10s fly", info: INFO.fly },
};

export function HistoryTab() {
  const [years, setYears] = useState<string>("30");
  const [pcaYears, setPcaYears] = useState<string>("10");
  const q = useApiQuery<CurveHistory>("/macro/curve/history", { years: +years, pca_years: +pcaYears });

  const controls = (
    <Controls>
      <div className="mc-inline-field">
        <span className="oc-field-label">History</span>
        <SegmentedControl size="sm" options={YEARS.map((y) => ({ value: y, label: `${y}Y` }))} value={years} onChange={setYears} ariaLabel="History window" />
      </div>
      <div className="mc-inline-field">
        <span className="oc-field-label">PCA window</span>
        <SegmentedControl size="sm" options={PCA_YEARS.map((y) => ({ value: y, label: `${y}Y` }))} value={pcaYears} onChange={setPcaYears} ariaLabel="PCA window" />
      </div>
    </Controls>
  );
  const method = (
    <MethodCard
      title="Three numbers describe most of the curve"
      formulas={["\\Sigma_{\\Delta y} = V\\Lambda V^{\\top}", "\\text{share}_k = \\lambda_k \\big/ \\textstyle\\sum_j \\lambda_j"]}
      refs={["Litterman & Scheinkman (1991), J. Fixed Income 1(1)", "Estrella & Mishkin (1998), REStat 80(1)"]}
    >
      Take every day's change in yields across maturities and find the few patterns that explain them. Historically ~80–90% of all moves are a parallel <em>level</em> shift, most of the rest a <em>slope</em> twist, and a little a <em>curvature</em> bend. Hedging those three factors hedges almost all rate risk.
    </MethodCard>
  );

  if (q.isError && !q.data) {
    return (
      <div className="stack">
        {controls}
        <div className="grid-3">
          <Panel title="Curve history" subtitle="Yields over time, curve slopes and inversions, and the principal components of rate moves." query={q} span={2} />
          {method}
        </div>
      </div>
    );
  }

  return (
    <div className="stack">
      {controls}
      <Panel<CurveHistory> query={q} skeletonHeight={96} notes={[]} provenance={[]}>
        {(d) => (
          <StatGrid min={150}>
            {Object.entries(d.spreads_latest).map(([k, v]) => (
              <StatTile key={k} label={SPREAD_INFO[k]?.label ?? k} value={fmtBps(v.value_bp / 1e4, 0, { signed: true })} tone={k !== "2s5s10s" && v.value_bp < 0 ? "loss" : "neutral"} info={SPREAD_INFO[k]?.info} caption={`${ordinal(v.percentile)} pct · ${fmtDate(v.date, "short")}`} />
            ))}
          </StatGrid>
        )}
      </Panel>
      <Panel<CurveHistory>
        title="Yields through time"
        subtitle="Each column is a month-end curve; colour is the yield. Read across for how rates evolved, down a column for the shape of the curve that month — a bright short end over a dimmer long end is an inversion."
        info={INFO.par}
        query={q}
        skeletonHeight={360}
        notes={[]}
      >
        {(d) => <YieldHeatmap d={d} />}
      </Panel>
      <Panel<CurveHistory>
        title="Curve slopes"
        subtitle="Long minus short yields in basis points. Shaded spans are periods when the 3m10y spread was inverted (below zero) — the signal that has preceded every U.S. recession since the late 1960s."
        info={INFO.s3m10y}
        query={q}
        skeletonHeight={340}
        notes={[]}
        provenance={[]}
      >
        {(d) => <Slopes d={d} />}
      </Panel>
      <div className="grid-3">
        <Panel<CurveHistory>
          title="PCA loadings"
          subtitle="How much each maturity moves per unit of each factor. Level is flat (everything moves together), slope crosses zero, curvature bows."
          info={INFO.pca}
          query={q}
          skeletonHeight={320}
          notes={[]}
          provenance={[]}
        >
          {(d) => <Loadings d={d} />}
        </Panel>
        <Panel<CurveHistory>
          title="Variance explained"
          subtitle="Share of daily yield-change variance captured by each principal component."
          info={INFO.pca}
          query={q}
          skeletonHeight={320}
          notes={[]}
          provenance={[]}
        >
          {(d) => {
            const ev = d.pca.explained_variance_all.slice(0, 6);
            return (
              <>
                <Bars series={[{ name: "Explained", x: ev.map((_, i) => (i < 3 ? ["Level", "Slope", "Curvature"][i] : `PC${i + 1}`)), y: ev }]} pct height={250} />
                <div className="subtle small">
                  <span className="num">{d.pca.n_obs.toLocaleString()}</span> daily changes, {fmtDate(d.pca.window.start, "month")} – {fmtDate(d.pca.window.end, "month")}. First three: <span className="num">{fmtPct(d.pca.explained_variance.reduce((a, b) => a + b, 0), 1)}</span>.
                </div>
              </>
            );
          }}
        </Panel>
        {method}
      </div>
      <Panel<CurveHistory>
        title="Factor levels"
        subtitle="Cumulative factor scores (bp), weekly: the level factor tracks the overall rate cycle, the slope factor steepening (+) and flattening (−). Scale and sign are normalised, so read the shape, not the level."
        info={INFO.pca}
        query={q}
        skeletonHeight={300}
      >
        {(d) => <FactorLevels d={d} />}
      </Panel>
    </div>
  );
}

function YieldHeatmap({ d }: { d: CurveHistory }) {
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const tenors = d.heatmap.tenors.map(tenorLabel);
      // z: tenors (rows) × dates (columns) so time runs left→right
      const z = d.heatmap.tenors.map((_, j) => d.heatmap.yields.map((row) => row[j] ?? null));
      return [
        {
          type: "heatmap",
          x: d.heatmap.dates,
          y: tenors,
          z,
          colorscale: t.sequential.map((c, i) => [i / (t.sequential.length - 1), c]),
          colorbar: { thickness: 8, outlinewidth: 0, ticksuffix: "%", tickfont: { family: t.fontMono, size: 10, color: t.text3 }, len: 0.9 },
          hovertemplate: "%{x|%b %Y} · %{y}<br><b>%{z:.2f}%</b><extra></extra>",
          hoverongaps: false,
        } as any,
      ];
    },
    [d],
  );
  const layout = useMemo(() => ({ xaxis: { type: "date", showspikes: false }, yaxis: { type: "category", showgrid: false, title: { text: "Maturity" } }, margin: { l: 8, r: 8, t: 8, b: 28 } }) as any, []);
  return <Chart data={data} layout={layout} height={340} ariaLabel="Yield heatmap" />;
}

function Slopes({ d }: { d: CurveHistory }) {
  const s = d.spreads;
  const data = useMemo(
    () => (t: Tokens): Data[] =>
      (["2s10s", "3m10y", "5s30s"] as const)
        .filter((c) => s.columns.includes(c))
        .map((c, i) => ({ type: "scatter", mode: "lines", name: c, x: s.index, y: s.data[c], line: { width: 1.5, color: t.categorical[i] }, connectgaps: false, hovertemplate: `<b>${c}</b> %{y:,.0f} bp<extra></extra>` })) as Data[],
    [s],
  );
  const layout = useMemo(
    () => (t: Tokens) => {
      const y = s.data["3m10y"] ?? [];
      const inv = runs(s.index, (i) => y[i] != null && (y[i] as number) < 0);
      return {
        hovermode: "x unified",
        xaxis: { type: "date", hoverformat: "%d %b %Y" },
        yaxis: { ticksuffix: " bp", side: "right", zeroline: true, zerolinecolor: t.ruleStrong },
        shapes: inv.map(([a, b]) => ({ type: "rect", xref: "x", yref: "paper", x0: a, x1: b, y0: 0, y1: 1, fillcolor: withAlpha(t.loss, 0.1), line: { width: 0 }, layer: "below" })),
        margin: { l: 16, r: 8, t: 36, b: 28 },
      } as any;
    },
    [s],
  );
  return (
    <>
      <Chart data={data} layout={layout} height={320} ariaLabel="Curve slopes" />
      <div className="mc-legend-row">
        <Swatch color="var(--loss-soft)" label="3m10y inverted" />
      </div>
    </>
  );
}

function Loadings({ d }: { d: CurveHistory }) {
  const L = d.pca.loadings;
  const data = useMemo(
    () => (t: Tokens): Data[] =>
      L.columns.map((c, i) => ({
        type: "scatter",
        mode: "lines+markers",
        name: c[0].toUpperCase() + c.slice(1),
        x: L.index,
        y: L.data[c],
        customdata: (L.index as number[]).map(tenorLabel),
        line: { width: 2, color: t.categorical[i] },
        marker: { size: 5 },
        hovertemplate: "%{customdata}  <b>%{y:.3f}</b><extra>%{fullData.name}</extra>",
      })) as Data[],
    [L],
  );
  const layout = useMemo(() => ({ xaxis: tenorAxis([Math.min(...(L.index as number[])), Math.max(...(L.index as number[]))]), yaxis: { tickformat: ".2f", zeroline: true, side: "right" }, hovermode: "x unified", margin: { l: 16, r: 8, t: 36, b: 40 } }) as any, [L]);
  return <Chart data={data} layout={layout} height={300} ariaLabel="PCA loadings" />;
}

function FactorLevels({ d }: { d: CurveHistory }) {
  const f = d.pca.factor_levels_weekly;
  const data = useMemo(
    () => (t: Tokens): Data[] =>
      f.columns.map((c, i) => ({ type: "scatter", mode: "lines", name: c[0].toUpperCase() + c.slice(1), x: f.index, y: f.data[c], line: { width: 1.6, color: t.categorical[i] }, hovertemplate: "<b>%{fullData.name}</b> %{y:,.0f} bp<extra></extra>" })) as Data[],
    [f],
  );
  const layout = useMemo(() => ({ hovermode: "x unified", xaxis: { type: "date", hoverformat: "%d %b %Y" }, yaxis: { ticksuffix: " bp", side: "right" }, margin: { l: 16, r: 8, t: 36, b: 28 } }) as any, []);
  return <Chart data={data} layout={layout} height={280} ariaLabel="PCA factor levels" />;
}
