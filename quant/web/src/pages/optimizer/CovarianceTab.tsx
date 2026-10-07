/**
 * COVARIANCE: the risk model under the chosen estimator (shrinkage, condition number, average
 * correlation, signal eigenvalues), the correlation matrix in HRP (quasi-diagonal) order with
 * its tree, the eigenvalue spectrum against the Marchenko–Pastur noise band, and the six
 * estimators side by side. POST /portfolio/covariance.
 */
import { useMemo, useState } from "react";
import { DataTable, HeatmapChart, Select, StatGrid, StatTile, type Column } from "../../components";
import { DendroChart } from "../../charts/DendroChart";
import { XYChart } from "../../charts/XYChart";
import { Note } from "../../design";
import { fmtNum, fmtPct } from "../../lib/format";
import { useDebounced } from "../../lib/hooks";
import { useApiPost } from "../../lib/query";
import { covarianceBody } from "./config";
import { COV_CODE, INFO } from "./info";
import { QPanel, Readline, sym, useOpt } from "./shared";
import type { CovName, CovarianceOut, LinkageName } from "./types";

export function CovarianceTab({ enabled }: { enabled: boolean }) {
  const { cfg, set } = useOpt();
  const [linkage, setLinkage] = useState<LinkageName>("single");
  const body = useDebounced(
    useMemo(() => covarianceBody(cfg, linkage), [cfg, linkage]),
    300,
  );
  const q = useApiPost<CovarianceOut>("/portfolio/covariance", body, { enabled: enabled && body.tickers.length >= 2 });
  const asOf = q.data?.universe.end;

  return (
    <div className="op-tab-body">
      <QPanel<CovarianceOut>
        q={q}
        title={
          <>
            Σ · {COV_CODE[cfg.cov]}
            <Note n={4} to="covariance" />
          </>
        }
        asOf={asOf}
        skeletonHeight={100}
        actions={<Select<CovName> ariaLabel="Estimator" value={cfg.cov} onChange={(cov) => set({ cov })} options={(Object.keys(COV_CODE) as CovName[]).map((k) => ({ value: k, label: COV_CODE[k] }))} />}
      >
        {(d) => {
          const row = d.comparison.find((c) => c.estimator === d.estimator);
          const sample = sampleCond(d);
          return (
            <StatGrid min={130}>
              <StatTile label={sym("Shrinkage δ")} value={d.shrinkage} format={(v) => fmtNum(v, 3)} info={INFO.shrinkage} caption={d.shrinkage == null ? "NOT SHRUNK" : "TO TARGET"} />
              <StatTile label={sym("Cond κ")} value={row?.condition_number ?? null} format={(v) => fmtNum(v, 0)} info={INFO.condition} caption={sample != null ? `SAMPLE ${fmtNum(sample, 0)}` : undefined} />
              <StatTile label={sym("Avg ρ")} value={row?.average_correlation ?? null} format={(v) => fmtNum(v, 2)} info={INFO.avgCorr} />
              <StatTile label={sym("Signal λ")} value={d.spectrum.n_signal} format={(v) => `${fmtNum(v, 0)} / ${d.spectrum.assets}`} info={INFO.mp} caption={`${fmtPct(d.spectrum.signal_variance_share, 0)} OF VAR`} />
              <StatTile label="T / N" value={d.spectrum.q} format={(v) => fmtNum(v, 0)} info={{ title: "Observations per asset", text: "Days of data per asset; the lower it is, the wider the noise band." }} caption={`${fmtNum(d.universe.observations, 0)}D · ${d.universe.tickers.length}`} />
            </StatGrid>
          );
        }}
      </QPanel>

      {!q.isError && (
        <>
          <div className="grid-2">
            <QPanel<CovarianceOut> q={q} title="Correlation · HRP order" info={INFO.corrDistance} asOf={asOf} skeletonHeight={380} notes={[]} provenance={[]}>
              {(d) => {
                const f = d.correlation_ordered;
                const z = f.index.map((_, i) => f.columns.map((c) => f.data[c][i]));
                return <HeatmapChart x={f.columns} y={f.index as string[]} z={z} diverging palette="neutral" zmin={-1} zmax={1} format="num" digits={2} showValues={f.columns.length <= 12} height={Math.max(340, Math.min(560, f.columns.length * 34 + 60))} />;
              }}
            </QPanel>
            <QPanel<CovarianceOut>
              q={q}
              title={`Tree · ${linkage.toUpperCase()}`}
              asOf={asOf}
              skeletonHeight={380}
              notes={[]}
              provenance={[]}
              actions={<Select<LinkageName> ariaLabel="Linkage" value={linkage} onChange={setLinkage} options={(["single", "ward", "average", "complete"] as LinkageName[]).map((l) => ({ value: l, label: l.toUpperCase() }))} />}
            >
              {(d) => <DendroChart d={d.dendrogram} height={Math.max(340, Math.min(560, d.dendrogram.ivl.length * 34 + 60))} ariaLabel="Correlation clustering tree" />}
            </QPanel>
          </div>

          <QPanel<CovarianceOut> q={q} title="Spectrum · Marchenko–Pastur" info={INFO.mp} asOf={asOf} skeletonHeight={320} notes={[]} provenance={[]}>
            {(d) => (
              <>
                <SpectrumLine d={d} />
                <div className="grid-2">
                  <EigenBars d={d} />
                  <MpDensity d={d} />
                </div>
              </>
            )}
          </QPanel>

          <QPanel<CovarianceOut> q={q} title="Estimators · same returns" asOf={asOf} flush skeletonHeight={240}>
            {(d) => <EstimatorTable d={d} onPick={(cov) => set({ cov })} />}
          </QPanel>
        </>
      )}
    </div>
  );
}

function sampleCond(d: CovarianceOut) {
  return d.comparison.find((c) => c.estimator === "sample")?.condition_number ?? null;
}

function SpectrumLine({ d }: { d: CovarianceOut }) {
  const s = d.spectrum;
  return (
    <Readline
      items={[
        { k: "SIGNAL", v: `${s.n_signal} / ${s.assets}` },
        { k: "VAR SHARE", v: fmtPct(s.signal_variance_share, 0) },
        { k: "λ1", v: `${fmtNum(s.sample_eigenvalues[0], 2)} · ${fmtPct(s.sample_eigenvalues[0] / s.assets, 0)}` },
        { k: "BAND", v: `${fmtNum(s.lambda_minus, 2)} – ${fmtNum(s.lambda_plus, 2)}` },
        { k: "T", v: fmtNum(s.observations, 0) },
        { k: "q = T/N", v: fmtNum(s.q, 0) },
        s.assets < 20 && { k: "N < 20", v: "MP COARSE", tone: "subtle" },
      ]}
    />
  );
}

function EigenBars({ d }: { d: CovarianceOut }) {
  const s = d.spectrum;
  const { x, series } = useMemo(() => {
    const x = s.sample_eigenvalues.map((_, i) => i + 1);
    const series = [
      { name: "SIGNAL", y: s.sample_eigenvalues.map((v) => (v > s.lambda_plus ? v : null)), mode: "bars" as const, tone: "ink" as const, label: false },
      { name: "NOISE", y: s.sample_eigenvalues.map((v) => (v > s.lambda_plus ? null : v)), mode: "bars" as const, tone: "ink3" as const, label: false },
      ...(d.estimator !== "sample" ? [{ name: COV_CODE[d.estimator], y: s.estimator_eigenvalues, mode: "points" as const, tone: "ink2" as const, size: 5, label: false }] : []),
    ];
    return { x, series };
  }, [s, d.estimator]);
  return <XYChart x={x} series={series} logY yMin={1e-3} digits={3} xFormat="int" xTitle="Rank" hlines={[{ at: s.lambda_plus, label: "λ+", tone: "ink2" }]} height={280} ariaLabel="Eigenvalues by rank against the Marchenko–Pastur noise ceiling" />;
}

/** The MP density for pure noise with the observed eigenvalues as marks on the axis. */
function MpDensity({ d }: { d: CovarianceOut }) {
  const s = d.spectrum;
  const { x, series } = useMemo(() => {
    const dens = (v: number) => {
      const g = s.mp_grid;
      if (!g.length || v < g[0] || v > g[g.length - 1]) return 0;
      let i = 1;
      while (i < g.length && g[i] < v) i++;
      const a = g[i - 1], b = g[i] ?? a;
      const t = b > a ? (v - a) / (b - a) : 0;
      return s.mp_density[i - 1] + t * ((s.mp_density[i] ?? s.mp_density[i - 1]) - s.mp_density[i - 1]);
    };
    const xs = [...new Set([...s.mp_grid, ...s.sample_eigenvalues])].sort((a, b) => a - b);
    const eig = new Set(s.sample_eigenvalues);
    return {
      x: xs,
      series: [
        { name: "MP", y: xs.map(dens), tone: "ink2" as const },
        { name: "EIG SIGNAL", y: xs.map((v) => (eig.has(v) && v > s.lambda_plus ? 0 : null)), mode: "points" as const, tone: "ink" as const, size: 7, label: false },
        { name: "EIG NOISE", y: xs.map((v) => (eig.has(v) && v <= s.lambda_plus ? 0 : null)), mode: "points" as const, tone: "ink3" as const, size: 7, label: false },
      ],
    };
  }, [s]);
  return <XYChart x={x} series={series} digits={2} xTitle="EIG" zero vlines={[{ at: s.lambda_plus, label: "λ+", tone: "ink2", dash: "dot" }]} height={280} ariaLabel="Marchenko–Pastur density with observed eigenvalues" />;
}

function EstimatorTable({ d, onPick }: { d: CovarianceOut; onPick: (c: CovName) => void }) {
  type Row = CovarianceOut["comparison"][number];
  const cols: Column<Row>[] = [
    {
      key: "estimator",
      label: "Estimator",
      render: (r) => (
        <span className={`num ${r.estimator === d.estimator ? "op-strong" : ""}`}>
          {COV_CODE[r.estimator]}
          {r.estimator === d.estimator && " · IN USE"}
        </span>
      ),
    },
    { key: "shrinkage", label: sym("δ"), numeric: true, format: (v) => fmtNum(v, 3), info: INFO.shrinkage },
    { key: "condition_number", label: sym("κ"), numeric: true, format: (v) => fmtNum(v, 0), info: INFO.condition },
    { key: "average_correlation", label: sym("Avg ρ"), numeric: true, format: (v) => fmtNum(v, 3), info: INFO.avgCorr },
    { key: "reference", label: "Ref", render: (r) => <span className="op-ref">{r.reference}</span>, sortable: false, hideBelow: 1200 },
  ];
  return <DataTable<Row> columns={cols} rows={d.comparison} rowKey={(r) => r.estimator} onRowClick={(r) => onPick(r.estimator)} isActive={(r) => r.estimator === d.estimator} />;
}
