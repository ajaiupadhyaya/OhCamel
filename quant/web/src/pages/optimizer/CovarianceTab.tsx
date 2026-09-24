/**
 * Covariance explorer: correlation heatmap in HRP (quasi-diagonal) order with its tree, the
 * eigenvalue spectrum against the Marchenko–Pastur noise band, and how the six estimators
 * compare on the same data. POST /portfolio/covariance.
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import {
  Chart,
  DataTable,
  HeatmapChart,
  Select,
  StatGrid,
  StatTile,
  withAlpha,
  type Column,
} from "../../components";
import { fmtNum, fmtPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import { useDebounced } from "../../lib/hooks";
import type { Tokens } from "../../lib/theme";
import { covarianceBody } from "./config";
import { Dendrogram } from "./Dendrogram";
import { COV_LABEL, COV_SHORT, INFO, LINKAGE_INFO, covInfo } from "./info";
import { QPanel, useOpt } from "./shared";
import type { CovName, CovarianceOut, LinkageName } from "./types";

export function CovarianceTab({ enabled }: { enabled: boolean }) {
  const { cfg, set } = useOpt();
  const [linkage, setLinkage] = useState<LinkageName>("single");
  const body = useDebounced(
    useMemo(() => covarianceBody(cfg, linkage), [cfg, linkage]),
    300,
  );
  const q = useApiPost<CovarianceOut>("/portfolio/covariance", body, {
    enabled: enabled && body.tickers.length >= 2,
  });
  const d = q.data;

  return (
    <div className="stack-lg">
      <QPanel<CovarianceOut>
        q={q}
        title="Risk model"
        info={covInfo(cfg.cov, d?.method.reference as string | undefined)}
        subtitle="The covariance matrix is the optimiser's whole view of risk. Shrinkage and denoising trade a little bias for a lot less noise; the condition number shows how fragile the matrix is to invert."
        skeletonHeight={100}
        actions={
          <Select<CovName>
            ariaLabel="Estimator"
            value={cfg.cov}
            onChange={(cov) => set({ cov })}
            options={(Object.keys(COV_LABEL) as CovName[]).map((k) => ({
              value: k,
              label: COV_LABEL[k],
            }))}
          />
        }
      >
        {(d) => {
          const row = d.comparison.find((c) => c.estimator === d.estimator);
          return (
            <StatGrid min={140}>
              <StatTile
                label="Shrinkage"
                value={d.shrinkage}
                format={(v) => fmtNum(v, 3)}
                info={INFO.shrinkage}
                caption={
                  d.shrinkage == null
                    ? "not a shrinkage estimator"
                    : "toward the target"
                }
              />
              <StatTile
                label="Condition number"
                value={row?.condition_number ?? null}
                format={(v) => fmtNum(v, 0)}
                info={INFO.condition}
                caption={
                  row && sampleCond(d)
                    ? `sample: ${fmtNum(sampleCond(d), 0)}`
                    : undefined
                }
              />
              <StatTile
                label="Avg. correlation"
                value={row?.average_correlation ?? null}
                format={(v) => fmtNum(v, 2)}
                info={INFO.avgCorr}
              />
              <StatTile
                label="Signal eigenvalues"
                value={d.spectrum.n_signal}
                format={(v) => `${fmtNum(v, 0)} of ${d.spectrum.assets}`}
                info={INFO.mp}
                caption={`${fmtPct(d.spectrum.signal_variance_share, 0)} of variance`}
              />
              <StatTile
                label="T / N"
                value={d.spectrum.q}
                format={(v) => fmtNum(v, 0)}
                info={{
                  title: "Observations per asset",
                  text: "Days of data per asset. The lower it is, the wider the noise band and the more an optimiser is fooled by estimation error.",
                }}
                caption={`${d.universe.observations.toLocaleString()} days · ${d.universe.tickers.length} assets`}
              />
            </StatGrid>
          );
        }}
      </QPanel>

      {!q.isError && (
        <>
          <div className="grid-2">
            <QPanel<CovarianceOut>
              q={q}
              title="Correlation, clustered"
              info={INFO.corrDistance}
              subtitle="Rows and columns re-ordered by the HRP tree (quasi-diagonalisation), so blocks of assets that move together sit along the diagonal."
              skeletonHeight={380}
              notes={[]}
              provenance={[]}
            >
              {(d) => {
                const f = d.correlation_ordered;
                const z = f.index.map((_, i) =>
                  f.columns.map((c) => f.data[c][i]),
                );
                return (
                  <HeatmapChart
                    x={f.columns}
                    y={f.index as string[]}
                    z={z}
                    diverging
                    palette="neutral"
                    zmin={-1}
                    zmax={1}
                    format="num"
                    digits={2}
                    showValues={f.columns.length <= 12}
                    height={Math.max(
                      340,
                      Math.min(560, f.columns.length * 34 + 60),
                    )}
                  />
                );
              }}
            </QPanel>
            <QPanel<CovarianceOut>
              q={q}
              title="Cluster tree"
              info={INFO.corrDistance}
              subtitle={LINKAGE_INFO[linkage]}
              skeletonHeight={380}
              notes={[]}
              provenance={[]}
              actions={
                <Select<LinkageName>
                  ariaLabel="Linkage"
                  value={linkage}
                  onChange={setLinkage}
                  options={(
                    ["single", "ward", "average", "complete"] as LinkageName[]
                  ).map((l) => ({
                    value: l,
                    label: `${l[0].toUpperCase()}${l.slice(1)} linkage`,
                  }))}
                />
              }
            >
              {(d) => (
                <Dendrogram
                  d={d.dendrogram}
                  height={Math.max(
                    340,
                    Math.min(560, d.dendrogram.ivl.length * 34 + 60),
                  )}
                />
              )}
            </QPanel>
          </div>

          <QPanel<CovarianceOut>
            q={q}
            title="Signal or noise? The eigenvalue spectrum"
            info={INFO.mp}
            subtitle="Each eigenvalue is the variance of one independent “direction” in the correlation matrix. Random data with the same T and N would put every eigenvalue inside the shaded band; only those above it are structure you can trust."
            skeletonHeight={320}
            notes={[]}
            provenance={[]}
          >
            {(d) => (
              <div className="op-spectrum">
                <div className="op-spectrum-charts">
                  <EigenBars d={d} />
                  <MpDensity d={d} />
                </div>
                <SpectrumStory d={d} />
              </div>
            )}
          </QPanel>

          <QPanel<CovarianceOut>
            q={q}
            title="Estimators side by side"
            info={{
              text: "All six estimators on the same returns. Click a row to use that estimator everywhere on the page.",
            }}
            subtitle="Same data, six answers. Lower condition numbers are safer to optimise on; average correlation shows how much each one pulls correlations together."
            flush
            skeletonHeight={240}
          >
            {(d) => <EstimatorTable d={d} onPick={(cov) => set({ cov })} />}
          </QPanel>
        </>
      )}
    </div>
  );
}

function sampleCond(d: CovarianceOut) {
  return (
    d.comparison.find((c) => c.estimator === "sample")?.condition_number ?? null
  );
}

function EigenBars({ d }: { d: CovarianceOut }) {
  const s = d.spectrum;
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => {
        const ranks = s.sample_eigenvalues.map((_, i) => i + 1);
        const out: Data[] = [
          {
            type: "bar",
            name: "Sample eigenvalue",
            x: ranks,
            y: s.sample_eigenvalues,
            marker: {
              color: s.sample_eigenvalues.map((v) =>
                v > s.lambda_plus ? t.categorical[0] : t.unknown,
              ),
              opacity: 0.9,
            },
            hovertemplate:
              "λ<sub>%{x}</sub> = <b>%{y:.3f}</b><extra>sample</extra>",
          } as Data,
        ];
        if (d.estimator !== "sample")
          out.push({
            type: "scatter",
            mode: "markers",
            name: COV_SHORT[d.estimator],
            x: ranks,
            y: s.estimator_eigenvalues,
            marker: {
              symbol: "line-ew-open",
              size: 18,
              line: { width: 2, color: t.text },
            },
            hovertemplate: `λ<sub>%{x}</sub> = <b>%{y:.3f}</b><extra>${COV_SHORT[d.estimator]}</extra>`,
          } as Data);
        return out;
      },
    [s, d.estimator],
  );
  const layout = useMemo(
    () => (t: Tokens) => ({
      bargap: 0.35,
      showlegend: d.estimator !== "sample",
      legend: { orientation: "h", y: 1.02, yanchor: "bottom" },
      margin: { l: 8, r: 8, t: 30, b: 40 },
      xaxis: {
        title: { text: "Eigenvalue rank" },
        dtick: 1,
        showspikes: false,
      },
      yaxis: { title: { text: "Eigenvalue" }, type: "log", side: "right" },
      shapes: [
        {
          type: "rect",
          xref: "paper",
          x0: 0,
          x1: 1,
          y0: Math.max(s.lambda_minus, 1e-3),
          y1: s.lambda_plus,
          fillcolor: t.unknown,
          opacity: 0.12,
          line: { width: 0 },
          layer: "below",
        },
        {
          type: "line",
          xref: "paper",
          x0: 0,
          x1: 1,
          y0: s.lambda_plus,
          y1: s.lambda_plus,
          line: { color: t.unknown, width: 1.2, dash: "dot" },
        },
      ],
      annotations: [
        {
          xref: "paper",
          x: 1,
          y: Math.log10(s.lambda_plus),
          xanchor: "right",
          yanchor: "bottom",
          showarrow: false,
          text: `λ₊ = ${fmtNum(s.lambda_plus, 2)} noise ceiling`,
          font: { size: 10, color: t.text2 },
        },
      ],
    }),
    [s, d.estimator],
  );
  return (
    <Chart
      data={data}
      layout={layout as never}
      height={300}
      ariaLabel="Eigenvalues versus the Marchenko-Pastur band"
    />
  );
}

function MpDensity({ d }: { d: CovarianceOut }) {
  const s = d.spectrum;
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => [
        {
          type: "scatter",
          mode: "lines",
          name: "Marchenko–Pastur density (pure noise)",
          x: s.mp_grid,
          y: s.mp_density,
          line: { color: t.unknown, width: 2 },
          fill: "tozeroy",
          fillcolor: withAlpha(t.unknown, 0.14),
          hovertemplate: "λ %{x:.2f}: density %{y:.2f}<extra></extra>",
        } as Data,
        {
          type: "scatter",
          mode: "markers",
          name: "Observed eigenvalues",
          x: s.sample_eigenvalues,
          y: s.sample_eigenvalues.map(() => 0),
          marker: {
            symbol: "line-ns-open",
            size: 22,
            line: {
              width: 2.2,
              color: s.sample_eigenvalues.map((v) =>
                v > s.lambda_plus ? t.categorical[0] : t.text3,
              ),
            },
          },
          hovertemplate: "λ = <b>%{x:.3f}</b><extra></extra>",
        } as Data,
      ],
    [s],
  );
  const layout = useMemo(
    () => ({
      showlegend: true,
      legend: {
        orientation: "h",
        y: 1.02,
        yanchor: "bottom",
        font: { size: 11 },
      },
      margin: { l: 8, r: 8, t: 30, b: 40 },
      xaxis: {
        title: { text: "Eigenvalue" },
        rangemode: "tozero",
        showspikes: false,
      },
      yaxis: { title: { text: "Density" }, side: "right", rangemode: "tozero" },
    }),
    [],
  );
  return (
    <Chart
      data={data}
      layout={layout as never}
      height={300}
      ariaLabel="Marchenko-Pastur density"
    />
  );
}

function SpectrumStory({ d }: { d: CovarianceOut }) {
  const s = d.spectrum;
  const top = s.sample_eigenvalues[0];
  return (
    <div className="op-story">
      <p>
        <b className="num">{s.n_signal}</b> of{" "}
        <span className="num">{s.assets}</span> eigenvalues rise above the noise
        ceiling λ₊ ≈ <span className="num">{fmtNum(s.lambda_plus, 2)}</span> and
        together explain{" "}
        <b className="num">{fmtPct(s.signal_variance_share, 0)}</b> of the total
        variance.
        {top > s.lambda_plus && (
          <>
            {" "}
            The largest (<span className="num">{fmtNum(top, 2)}</span>,{" "}
            <span className="num">{fmtPct(top / s.assets, 0)}</span> of
            variance) is typically the “market” — everything rising and falling
            together.
          </>
        )}
      </p>
      <p className="subtle">
        The rest sit inside the band{" "}
        <span className="num">
          [{fmtNum(s.lambda_minus, 2)}, {fmtNum(s.lambda_plus, 2)}]
        </span>{" "}
        that random, uncorrelated returns would produce with{" "}
        <span className="num">{s.observations.toLocaleString()}</span> days and{" "}
        <span className="num">{s.assets}</span> assets (q = T/N ={" "}
        <span className="num">{fmtNum(s.q, 0)}</span>). An optimiser that
        inverts the matrix leans hardest on exactly these small, noisy
        directions — which is why denoising or shrinkage usually helps out of
        sample.
      </p>
      {s.assets < 20 && (
        <p className="subtle small">
          With fewer than ~20 assets the Marchenko–Pastur law (an N, T → ∞
          result) gives only a coarse split; read it as a guide, not a test.
        </p>
      )}
    </div>
  );
}

function EstimatorTable({
  d,
  onPick,
}: {
  d: CovarianceOut;
  onPick: (c: CovName) => void;
}) {
  type Row = CovarianceOut["comparison"][number];
  const cols: Column<Row>[] = [
    {
      key: "estimator",
      label: "Estimator",
      render: (r) => (
        <span className={r.estimator === d.estimator ? "op-strong" : ""}>
          {COV_LABEL[r.estimator]}
          {r.estimator === d.estimator && (
            <span className="badge accent" style={{ marginLeft: 8 }}>
              in use
            </span>
          )}
        </span>
      ),
    },
    {
      key: "shrinkage",
      label: "Shrinkage",
      numeric: true,
      format: (v) => (v == null ? "—" : fmtNum(v, 3)),
      info: INFO.shrinkage,
    },
    {
      key: "condition_number",
      label: "Condition no.",
      numeric: true,
      format: (v) => fmtNum(v, 0),
      info: INFO.condition,
    },
    {
      key: "average_correlation",
      label: "Avg. corr.",
      numeric: true,
      format: (v) => fmtNum(v, 3),
      info: INFO.avgCorr,
    },
    {
      key: "reference",
      label: "Reference",
      render: (r) => (
        <span className="subtle small op-wrap">{r.reference}</span>
      ),
      sortable: false,
      hideBelow: 900,
    },
  ];
  return (
    <DataTable<Row>
      columns={cols}
      rows={d.comparison}
      rowKey={(r) => r.estimator}
      onRowClick={(r) => onPick(r.estimator)}
      isActive={(r) => r.estimator === d.estimator}
    />
  );
}
