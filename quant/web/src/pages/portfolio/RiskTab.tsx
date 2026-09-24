/**
 * Risk — VaR/ES across nine models (POST /risk/summary), Euler decomposition
 * (/risk/decomposition), out-of-sample backtests (/risk/backtest) and GARCH volatility
 * (/risk/garch). The backtest is the heavy one (seconds), so it runs on an explicit button.
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, Field, HeatmapChart, InfoTip, Panel, Section, SegmentedControl, Select, StatGrid, StatTile, TimeSeriesChart, Toggle, withAlpha, type Column } from "../../components";
import { Bars } from "./Bars";
import { fmtCurrency, fmtDate, fmtMultiple, fmtNum, fmtPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import { frameToMatrix } from "../../lib/series";
import type { Tokens } from "../../lib/theme";
import type { PortfolioIn } from "../../lib/types";
import { CONDITIONAL_MODELS, FAT_TAIL_MODELS, INFO, modelInfo, modelLabel } from "./info";
import { KV, PValue, PctUsd, Reading, RunButton, ZoneChip, asDates, useCommitted, usd } from "./shared";
import type { BacktestOut, DecompPosition, DecompositionOut, Estimate, GarchOut, RiskSummaryOut, Scorecard } from "./types";

type Alpha = "0.95" | "0.99";
const ALPHAS: { value: Alpha; label: string }[] = [
  { value: "0.95", label: "95%" },
  { value: "0.99", label: "99%" },
];

export function RiskTab({ req }: { req: PortfolioIn }) {
  return (
    <>
      <ModelsSection req={req} />
      <DecompositionSection req={req} />
      <BacktestSection req={req} />
      <GarchSection req={req} />
    </>
  );
}

// =================================================================== summary

function isInvalid(e: Estimate | undefined): boolean {
  return !!e && (e.var == null || (e.params as { valid_domain?: boolean } | undefined)?.valid_domain === false);
}

function ModelsSection({ req }: { req: PortfolioIn }) {
  const [horizon, setHorizon] = useState<"1" | "10">("1");
  const [alpha, setAlpha] = useState<Alpha>("0.99");
  const body = useMemo(() => ({ ...req, alphas: [0.99, 0.95], horizon: Number(horizon) }), [req, horizon]);
  const q = useApiPost<RiskSummaryOut>("/risk/summary", body);
  const notional = req.notional ?? 1_000_000;
  return (
    <Section
      title="How much could you lose?"
      description="Value at Risk is a loss that should be exceeded only on the worst 1% (or 5%) of days; Expected Shortfall is the average loss on those days. Nine models answer the same question with different assumptions — their disagreement is itself information."
      actions={
        <>
          <SegmentedControl size="sm" ariaLabel="Horizon" options={[{ value: "1", label: "1 day" }, { value: "10", label: "10 days" }]} value={horizon} onChange={setHorizon} />
        </>
      }
    >
      <Panel<RiskSummaryOut> title="Model comparison" subtitle={`${horizon}-day loss as a share of equity and in dollars of the ${fmtCurrency(notional, { compact: true })} notional. Hover a model name for its assumptions.`} info="var" query={q} skeletonHeight={380} flush notes={[]}>
        {(d) => (
          <>
            <div className="pl-pad">
              <Reading>{summaryReading(d, notional)}</Reading>
            </div>
            <ModelTable d={d} />
          </>
        )}
      </Panel>
      <div className="grid-3">
        <Panel<RiskSummaryOut>
          span={2}
          title={`VaR and ES by model · ${alpha === "0.99" ? "99%" : "95%"}`}
          subtitle="Sorted by VaR. The gap between a model's VaR and ES bar shows how heavy it thinks the tail beyond VaR is."
          info="es"
          query={q}
          skeletonHeight={300}
          actions={<SegmentedControl size="sm" ariaLabel="Confidence" options={ALPHAS} value={alpha} onChange={setAlpha} />}
        >
          {(d) => <ModelBars d={d} alpha={alpha} />}
        </Panel>
        <Panel<RiskSummaryOut> title="Are returns normal?" subtitle="Daily portfolio returns against a fitted normal and Student-t curve. Fat tails are why models disagree." info={INFO.jb} query={q} skeletonHeight={300} notes={[]}>
          {(d) => <DistributionFit d={d} />}
        </Panel>
      </div>
    </Section>
  );
}

function summaryReading(d: RiskSummaryOut, notional: number): string {
  const at = d.estimates.filter((e) => Math.abs(e.alpha - 0.99) < 1e-9);
  const valid = at.filter((e) => !isInvalid(e) && e.var != null) as (Estimate & { var: number })[];
  if (valid.length < 2) return "Too few models produced an estimate to compare.";
  const lo = valid.reduce((a, b) => (b.var < a.var ? b : a));
  const hi = valid.reduce((a, b) => (b.var > a.var ? b : a));
  const mean = (ks: string[]) => {
    const xs = valid.filter((e) => ks.includes(e.model)).map((e) => e.var);
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  };
  const cond = mean(CONDITIONAL_MODELS);
  const uncond = mean(["historical", "gaussian", "student_t", "evt_pot"]);
  const fat = mean(FAT_TAIL_MODELS);
  const gauss = valid.find((e) => e.model === "gaussian")?.var ?? null;
  const h = d.horizon === 1 ? "one-day" : `${d.horizon}-day`;
  const parts = [
    `At 99%, ${h} VaR ranges from ${fmtPct(lo.var, 2)} (${modelLabel(lo.model)}) to ${fmtPct(hi.var, 2)} (${modelLabel(hi.model)}) — ${usd(lo.var * notional)} to ${usd(hi.var * notional)}, a ${fmtMultiple(hi.var / lo.var, 1)} spread.`,
  ];
  if (cond != null && uncond != null) {
    const rel = cond / uncond - 1;
    if (Math.abs(rel) >= 0.05)
      parts.push(
        rel < 0
          ? `Models that track current volatility sit ${fmtPct(-rel, 0)} below the full-history ones: markets are calmer now than on average, and these estimates will rise quickly if that changes.`
          : `Models that track current volatility sit ${fmtPct(rel, 0)} above the full-history ones: markets are more turbulent now than on average.`,
      );
    else parts.push("Volatility-tracking and full-history models agree to within 5%: current conditions look like the long-run average.");
  }
  if (fat != null && gauss != null && fat > gauss * 1.05) parts.push(`Fat-tail models are ${fmtPct(fat / gauss - 1, 0)} above the Gaussian, so a bell-curve estimate understates the loss on a truly bad day.`);
  const cf = at.find((e) => e.model === "cornish_fisher");
  if (cf && isInvalid(cf)) parts.push("Cornish–Fisher is excluded: the skew/kurtosis are outside its domain of validity.");
  return parts.join(" ");
}

type ModelRow = { model: string; e95?: Estimate; e99?: Estimate };

function ModelTable({ d }: { d: RiskSummaryOut }) {
  const rows = useMemo<ModelRow[]>(() => {
    const m = new Map<string, ModelRow>();
    for (const e of d.estimates) {
      const r = m.get(e.model) ?? { model: e.model };
      if (Math.abs(e.alpha - 0.95) < 1e-9) r.e95 = e;
      if (Math.abs(e.alpha - 0.99) < 1e-9) r.e99 = e;
      m.set(e.model, r);
    }
    return [...m.values()];
  }, [d]);
  const cell = (k: "e95" | "e99", f: "var" | "es"): Column<ModelRow> => ({
    key: `${k}_${f}`,
    label: `${f === "var" ? "VaR" : "ES"} ${k === "e95" ? "95" : "99"}`,
    numeric: true,
    value: (r) => r[k]?.[f] ?? null,
    render: (r) => (r[k]?.[f] == null ? <span className="subtle" title={r[k]?.error}>n/a</span> : <PctUsd pct={r[k]![f]} usd={r[k]![`${f}_usd`]} />),
    info: f === "var" ? "var" : "es",
    hideBelow: k === "e95" ? 600 : undefined,
  });
  const cols: Column<ModelRow>[] = [
    {
      key: "model",
      label: "Model",
      render: (r) => {
        const notes = [...(r.e99?.notes ?? []), ...(r.e95?.notes ?? [])];
        const flagged = isInvalid(r.e99) || isInvalid(r.e95);
        return (
          <span className="pl-model">
            <span className="pl-model-name">{modelLabel(r.model)}</span>
            <InfoLite info={modelInfo(r.model)} ref2={r.e99?.reference} />
            {flagged && (
              <span className="badge warn" title={notes.join(" ")}>
                unreliable
              </span>
            )}
          </span>
        );
      },
    },
    cell("e95", "var"),
    cell("e95", "es"),
    cell("e99", "var"),
    cell("e99", "es"),
    {
      key: "tail",
      label: "ES / VaR 99",
      numeric: true,
      value: (r) => (r.e99?.es != null && r.e99?.var ? r.e99.es / r.e99.var : null),
      format: (v) => fmtMultiple(v, 2),
      hideBelow: 900,
      info: { text: "How much worse the average tail day is than the VaR threshold. About 1.15 for a normal distribution at 99%; larger means a heavier tail." },
    },
  ];
  return <DataTable rows={rows} columns={cols} rowKey={(r) => r.model} />;
}

/** Info tip that appends the server's reference string for the model. */
function InfoLite({ info, ref2 }: { info: ReturnType<typeof modelInfo>; ref2?: string }) {
  return <InfoTip info={{ ...info, reference: ref2 ?? info.reference }} />;
}

function ModelBars({ d, alpha }: { d: RiskSummaryOut; alpha: Alpha }) {
  const rows = d.estimates.filter((e) => Math.abs(e.alpha - Number(alpha)) < 1e-9 && e.var != null).sort((a, b) => (a.var ?? 0) - (b.var ?? 0));
  const x = rows.map((r) => modelLabel(r.model) + (isInvalid(r) ? " *" : ""));
  return (
    <>
      <Bars
        horizontal
        height={rows.length * 44 + 50}
        series={[
          { name: "VaR", x, y: rows.map((r) => r.var) },
          { name: "Expected Shortfall", x, y: rows.map((r) => r.es) },
        ]}
        yFormat="pct"
        digits={2}
      />
      {rows.some(isInvalid) && <div className="subtle small">* outside the model's domain of validity — shown for completeness, not to be relied on.</div>}
    </>
  );
}

function DistributionFit({ d }: { d: RiskSummaryOut }) {
  const dist = d.distribution;
  const s = d.stats;
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const out: Data[] = [
        { type: "bar", name: "Observed", x: dist.centers, y: dist.counts, marker: { color: withAlpha(t.categorical[0], 0.55) }, hovertemplate: "%{x:.2%}: <b>%{y} days</b><extra></extra>" } as Data,
        { type: "scatter", mode: "lines", name: "Normal", x: dist.centers, y: dist.normal_expected, line: { color: t.categorical[1], width: 1.6 }, hovertemplate: "Normal %{y:.1f}<extra></extra>" } as Data,
      ];
      if (dist.t_expected) out.push({ type: "scatter", mode: "lines", name: "Student-t", x: dist.centers, y: dist.t_expected, line: { color: t.categorical[2], width: 1.6, dash: "dot" }, hovertemplate: "Student-t %{y:.1f}<extra></extra>" } as Data);
      return out;
    },
    [dist],
  );
  const layout = useMemo(() => ({ bargap: 0.02, margin: { l: 8, r: 8, t: 30, b: 30 }, xaxis: { tickformat: ".1%", showspikes: false }, yaxis: { type: "log", dtick: 1, side: "right", title: { text: "days (log)" }, range: [-0.3, Math.log10(Math.max(...dist.counts, 1)) + 0.2] }, showlegend: true }), [dist]);
  return (
    <>
      <Chart data={data} layout={layout as any} height={220} />
      <KV
        rows={[
          { label: "Skewness", value: fmtNum(s.skew, 2), info: INFO.skew },
          { label: "Excess kurtosis", value: fmtNum(s.excess_kurtosis, 1), info: INFO.kurtosis },
          { label: "Jarque–Bera", value: fmtNum(s.jarque_bera.stat, 0), info: INFO.jb, hint: s.jarque_bera.p_value < 0.001 ? "p < 0.001" : `p = ${fmtNum(s.jarque_bera.p_value, 3)}` },
          { label: "Vol (ann.)", value: fmtPct(s.vol_annualized, 1), info: "vol" },
        ]}
      />
      <div className="subtle small">Log scale: the tails, where the normal curve drops to almost nothing but real days keep appearing, are what matter for VaR.</div>
    </>
  );
}

// =================================================================== decomposition

function DecompositionSection({ req }: { req: PortfolioIn }) {
  const [alpha, setAlpha] = useState<Alpha>("0.99");
  const [cov, setCov] = useState<"sample" | "ewma">("sample");
  const body = useMemo(() => ({ ...req, alpha: Number(alpha), cov_method: cov }), [req, alpha, cov]);
  const q = useApiPost<DecompositionOut>("/risk/decomposition", body);
  return (
    <Section
      title="Where the risk sits"
      description="Capital weights say where the money is; risk contributions say where the losses would come from. Euler contributions add up exactly to portfolio VaR."
      actions={
        <>
          <SegmentedControl size="sm" ariaLabel="Confidence" options={ALPHAS} value={alpha} onChange={setAlpha} />
          <SegmentedControl size="sm" ariaLabel="Covariance" options={[{ value: "sample", label: "Sample", title: "Equal-weighted covariance over the full window" }, { value: "ewma", label: "EWMA", title: "RiskMetrics λ = 0.94: recent days dominate" }]} value={cov} onChange={setCov} />
        </>
      }
    >
      <Panel<DecompositionOut> query={q} skeletonHeight={110} notes={[]} compact>
        {(d) => <DecompHeadline d={d} />}
      </Panel>
      <div className="grid-2">
        <Panel<DecompositionOut> title="Capital vs risk" subtitle="Each holding's share of capital next to its share of parametric VaR and of historical ES. Bars taller than the weight are the risk concentrations." info={INFO.component_var} query={q} skeletonHeight={320} notes={[]}>
          {(d) => <CapitalVsRisk d={d} />}
        </Panel>
        <Panel<DecompositionOut> title="Correlation" subtitle="Daily return correlations over the analysis window. Low or negative pairs are where the diversification comes from." info={INFO.correlation} query={q} skeletonHeight={320} notes={[]}>
          {(d) => {
            const m = frameToMatrix(d.correlation);
            return <HeatmapChart x={m.x} y={m.y} z={m.z} diverging palette="neutral" zmin={-1} zmax={1} format="num" digits={2} showValues={m.x.length <= 10} height={320} />;
          }}
        </Panel>
      </div>
      <Panel<DecompositionOut> title="Position risk detail" subtitle="Marginal: VaR change per unit of extra weight. Incremental: VaR change if the position were removed entirely. Stand-alone: the position's VaR on its own." query={q} flush skeletonHeight={260}>
        {(d) => <DecompTable d={d} />}
      </Panel>
    </Section>
  );
}

function DecompHeadline({ d }: { d: DecompositionOut }) {
  const t = d.totals;
  const b = d.benchmark;
  const lvl = fmtPct(d.alpha, 0);
  return (
    <StatGrid min={140}>
      <StatTile label={`Parametric VaR ${lvl}`} value={fmtPct(t.parametric_var, 2)} caption={<span className="num">{usd(t.parametric_var_usd)}</span>} info={INFO.m_gaussian} />
      <StatTile label={`Historical VaR ${lvl}`} value={fmtPct(t.historical_var, 2)} caption={<span className="num">{usd(t.historical_var_usd)}</span>} info={INFO.m_historical} />
      <StatTile label={`Historical ES ${lvl}`} value={fmtPct(t.historical_es, 2)} caption={<span className="num">{usd(t.historical_es_usd)}</span>} info="es" />
      <StatTile label="Diversification ratio" value={fmtMultiple(t.diversification_ratio, 2)} info={INFO.diversification_ratio} />
      <StatTile label="Diversification gain" value={usd(t.diversification_benefit_var_usd)} tone="gain" caption={`${fmtPct(t.diversification_benefit_var / t.sum_standalone_var, 0)} of stand-alone VaR`} info={{ text: "Sum of the positions' stand-alone VaRs minus the portfolio VaR: the loss that correlation below 1 saves you." }} />
      {b && <StatTile label={`Beta to ${b.benchmark}`} value={fmtNum(b.portfolio_beta, 2)} info="beta" caption={<span className="num">TE {fmtPct(b.tracking_error_annualized, 1)}</span>} />}
    </StatGrid>
  );
}

function CapitalVsRisk({ d }: { d: DecompositionOut }) {
  const rows = [...d.positions].sort((a, b) => b.pct_var - a.pct_var);
  const x = rows.map((r) => r.ticker);
  const top = rows[0];
  return (
    <>
      {top && (
        <Reading>
          {top.ticker} is {fmtPct(top.weight, 0)} of capital but {fmtPct(top.pct_var, 0)} of {fmtPct(d.alpha, 0)} VaR
          {top.pct_var > top.weight * 1.2 ? " — the portfolio's main risk concentration." : "."}
        </Reading>
      )}
      <Bars
        series={[
          { name: "Weight", x, y: rows.map((r) => r.weight) },
          { name: "Share of VaR", x, y: rows.map((r) => r.pct_var) },
          { name: "Share of hist. ES", x, y: rows.map((r) => r.hist_pct_es) },
        ]}
        yFormat="pct"
        digits={1}
        height={290}
      />
    </>
  );
}

function DecompTable({ d }: { d: DecompositionOut }) {
  const beta = d.benchmark?.position_beta ?? {};
  const cols: Column<DecompPosition>[] = [
    { key: "ticker", label: "Holding", render: (r) => <strong className="num">{r.ticker}</strong> },
    { key: "weight", label: "Weight", numeric: true, format: (v) => fmtPct(v, 1) },
    { key: "vol_annualized", label: "Vol", numeric: true, format: (v) => fmtPct(v, 1), info: "vol", hideBelow: 900 },
    { key: "beta", label: "Beta", numeric: true, value: (r) => beta[r.ticker] ?? null, format: (v) => fmtNum(v, 2), info: "beta", hideBelow: 1200 },
    { key: "marginal_var", label: "Marginal VaR", numeric: true, format: (v) => fmtPct(v, 2), info: INFO.marginal_var, hideBelow: 900 },
    { key: "component_var", label: "Component VaR", numeric: true, render: (r) => <PctUsd pct={r.component_var} usd={r.component_var_usd} />, value: (r) => r.component_var, info: INFO.component_var },
    { key: "pct_var", label: "% of VaR", numeric: true, format: (v) => fmtPct(v, 1), heat: { min: 0, max: Math.max(0.3, ...d.positions.map((p) => p.pct_var)) } },
    { key: "hist_component_es", label: "Hist. ES contrib.", numeric: true, render: (r) => <PctUsd pct={r.hist_component_es} usd={r.hist_component_es_usd} />, value: (r) => r.hist_component_es, info: { text: "The position's average loss on the portfolio's worst (1 − α) days. These sum exactly to historical ES." }, hideBelow: 600 },
    { key: "incremental_var", label: "Incremental VaR", numeric: true, render: (r) => <PctUsd pct={r.incremental_var} usd={r.incremental_var_usd} />, value: (r) => r.incremental_var, info: INFO.incremental_var, hideBelow: 1200 },
  ];
  return <DataTable rows={d.positions} columns={cols} rowKey={(r) => r.ticker} defaultSort={{ key: "pct_var", dir: "desc" }} />;
}

// =================================================================== backtest

const WINDOWS = ["250", "500", "1000"] as const;

function BacktestSection({ req }: { req: PortfolioIn }) {
  const [alpha, setAlpha] = useState<Alpha>("0.99");
  const [window, setWindow] = useState<(typeof WINDOWS)[number]>("500");
  const [refit, setRefit] = useState<"5" | "20" | "60">("20");
  const draft = useMemo(() => ({ ...req, alpha: Number(alpha), window: Number(window), refit_every: Number(refit) }), [req, alpha, window, refit]);
  const { committed, run, dirty } = useCommitted(draft);
  const q = useApiPost<BacktestOut>("/risk/backtest", committed);
  const [model, setModel] = useState<string | null>(null);
  const shown = model ?? q.data?.ranking_by_fz0[0] ?? "historical";
  return (
    <Section
      title="Would the models have worked?"
      description="Each model is re-estimated on a rolling window and asked to forecast tomorrow's VaR, day after day, using only data available at the time. Then we count how often reality broke through."
      actions={
        <div className="pl-controls">
          <Field label="Confidence" inline>
            <SegmentedControl size="sm" ariaLabel="Confidence" options={ALPHAS} value={alpha} onChange={setAlpha} />
          </Field>
          <Field label="Window" inline info={{ text: "Sessions of history each forecast is estimated on." }}>
            <SegmentedControl size="sm" ariaLabel="Estimation window" options={WINDOWS.map((w) => ({ value: w, label: `${w}d` }))} value={window} onChange={setWindow} />
          </Field>
          <Field label="Refit" inline info={{ text: "How often the slower models (Student-t, GARCH, FHS, EVT) are re-estimated; in between, GARCH variances are still updated daily." }}>
            <SegmentedControl size="sm" ariaLabel="Refit frequency" options={[{ value: "5", label: "5d" }, { value: "20", label: "20d" }, { value: "60", label: "60d" }]} value={refit} onChange={setRefit} />
          </Field>
          <RunButton onRun={run} dirty={dirty} busy={q.isFetching} label="Run backtest" />
        </div>
      }
    >
      <Panel<BacktestOut>
        title="Backtest scorecard"
        subtitle={q.data ? `${q.data.series.dates.length.toLocaleString()} out-of-sample days, ${fmtDate(q.data.series.dates[0])} – ${fmtDate(q.data.series.dates[q.data.series.dates.length - 1])}. Models ranked by FZ0 loss (best first). Red p-values reject the model at 5%.` : "Rolling out-of-sample one-day forecasts."}
        info={{ text: "Coverage tests ask whether breaches happen as often as promised; independence tests ask whether they cluster; the ES test asks whether the loss beyond VaR is right. The Basel light applies to 99% VaR." }}
        query={q}
        flush
        skeletonHeight={360}
        actions={q.data ? <span className="subtle small num">computed in {fmtNum(q.data.compute_seconds, 1)} s</span> : undefined}
      >
        {(d) => <Scorecards d={d} active={shown} onPick={setModel} />}
      </Panel>
      <Panel<BacktestOut>
        title={`Realized P&L vs ${modelLabel(shown)} VaR`}
        subtitle="Each dot is a day's portfolio return. The band is the forecast VaR (solid) and ES (dotted); vermilion dots are breaches — days the loss exceeded that morning's VaR."
        info="var"
        query={q}
        skeletonHeight={360}
        notes={[]}
        actions={q.data ? <Select ariaLabel="Model" value={shown} onChange={setModel} options={q.data.ranking_by_fz0.map((m) => ({ value: m, label: modelLabel(m) }))} /> : undefined}
      >
        {(d) => <BacktestChart d={d} model={shown} />}
      </Panel>
    </Section>
  );
}

type CardRow = Scorecard & { model: string; rank: number };

function Scorecards({ d, active, onPick }: { d: BacktestOut; active: string; onPick: (m: string) => void }) {
  const rows: CardRow[] = d.ranking_by_fz0.map((m, i) => ({ ...d.scorecards[m], model: m, rank: i + 1 }));
  const is99 = Math.abs(d.alpha - 0.99) < 1e-9;
  const cols: Column<CardRow>[] = [
    { key: "rank", label: "#", numeric: true, width: 28, info: INFO.fz0 },
    {
      key: "model",
      label: "Model",
      render: (r) => (
        <span className="pl-model">
          <span className="pl-model-name">{modelLabel(r.model)}</span>
          <InfoTip info={modelInfo(r.model)} size={12} />
        </span>
      ),
    },
    {
      key: "exceptions",
      label: "Breaches",
      numeric: true,
      render: (r) => (
        <span title={`${r.exceptions} breaches vs ${fmtNum(r.expected, 1)} expected`}>
          <strong>{r.exceptions}</strong>
          <span className="subtle"> / {fmtNum(r.expected, 0)}</span>
        </span>
      ),
      info: { text: "Days the loss exceeded the forecast VaR, against the number the confidence level promises." },
    },
    { key: "exception_rate", label: "Rate", numeric: true, format: (v) => fmtPct(v, 2), hideBelow: 900, color: (v) => (Math.abs(v - (1 - d.alpha)) / (1 - d.alpha) > 0.5 ? "warn" : undefined) },
    { key: "kupiec", label: "Kupiec", numeric: true, value: (r) => r.kupiec.p_value, render: (r) => <PValue p={r.kupiec.p_value} />, info: INFO.kupiec },
    { key: "ind", label: "Indep.", numeric: true, value: (r) => r.christoffersen.p_value_ind, render: (r) => <PValue p={r.christoffersen.p_value_ind} />, info: INFO.christoffersen, hideBelow: 600 },
    { key: "cc", label: "CC", numeric: true, value: (r) => r.christoffersen.p_value_cc, render: (r) => <PValue p={r.christoffersen.p_value_cc} />, info: INFO.cc, hideBelow: 900 },
    { key: "dq", label: "DQ", numeric: true, value: (r) => r.dq?.p_value ?? null, render: (r) => <PValue p={r.dq?.p_value} />, info: INFO.dq, hideBelow: 900 },
    {
      key: "zone",
      label: "Basel",
      align: "center",
      value: (r) => ({ green: 0, yellow: 1, red: 2 })[r.traffic_light.zone],
      render: (r) => <ZoneChip zone={r.traffic_light.zone} title={`${r.traffic_light.exceptions} breaches in ${r.traffic_light.T} days; green ≤ ${r.traffic_light.green_max}, yellow ≤ ${r.traffic_light.yellow_max}`} />,
      info: INFO.traffic_light,
    },
    ...(is99
      ? [
          {
            key: "basel250",
            label: "Last 250d",
            align: "center",
            value: (r: CardRow) => r.basel_last_250?.exceptions ?? null,
            render: (r: CardRow) =>
              r.basel_last_250 ? (
                <span className="pl-basel">
                  <ZoneChip zone={r.basel_last_250.zone} title={`${r.basel_last_250.exceptions} breaches in the last ${r.basel_last_250.T} days`} />
                  <span className="subtle num small">×{fmtNum(r.basel_last_250.multiplier, 2)}</span>
                </span>
              ) : (
                "—"
              ),
            info: { text: "The regulatory view: breaches in the most recent 250 days and the capital multiplier (3 plus the plus-factor) a bank would face.", reference: "BCBS (1996)" },
            hideBelow: 1200,
          } as Column<CardRow>,
        ]
      : []),
    {
      key: "z2",
      label: "ES test",
      align: "center",
      value: (r) => r.acerbi_szekely_z2?.z2 ?? null,
      render: (r) =>
        r.acerbi_szekely_z2 ? (
          <span className={`badge ${r.acerbi_szekely_z2.indicative_verdict === "accept" ? "gain" : r.acerbi_szekely_z2.indicative_verdict === "reject" ? "loss" : "warn"}`} title={`${r.acerbi_szekely_z2.indicative_verdict}: Z₂ = ${fmtNum(r.acerbi_szekely_z2.z2, 2)} (5% critical ${fmtNum(r.acerbi_szekely_z2.critical_5pct, 1)})`}>
            {r.acerbi_szekely_z2.indicative_verdict.split(" ")[0]}
          </span>
        ) : (
          "—"
        ),
      info: INFO.z2,
      hideBelow: 600,
    },
  ];
  return <DataTable rows={rows} columns={cols} rowKey={(r) => r.model} onRowClick={(r) => onPick(r.model)} isActive={(r) => r.model === active} />;
}

function BacktestChart({ d, model }: { d: BacktestOut; model: string }) {
  const s = d.series;
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const v = s.var[model] ?? [];
      const e = s.es[model] ?? [];
      const exX: string[] = [];
      const exY: number[] = [];
      const okX: string[] = [];
      const okY: number[] = [];
      s.returns.forEach((r, i) => {
        const lim = v[i];
        if (lim != null && -r > lim) {
          exX.push(s.dates[i]);
          exY.push(r);
        } else {
          okX.push(s.dates[i]);
          okY.push(r);
        }
      });
      return [
        { type: "scattergl", mode: "markers", name: "Daily return", x: okX, y: okY, marker: { size: 3, color: withAlpha(t.text3, 0.45) }, hovertemplate: "%{x|%d %b %Y}  <b>%{y:.2%}</b><extra></extra>" } as Data,
        { type: "scatter", mode: "lines", name: "−VaR", x: s.dates, y: v.map((x) => (x == null ? null : -x)), line: { color: t.categorical[0], width: 1.4 }, hovertemplate: "−VaR %{y:.2%}<extra></extra>" } as Data,
        { type: "scatter", mode: "lines", name: "−ES", x: s.dates, y: e.map((x) => (x == null ? null : -x)), line: { color: t.categorical[1], width: 1, dash: "dot" }, hovertemplate: "−ES %{y:.2%}<extra></extra>" } as Data,
        { type: "scatter", mode: "markers", name: `Breaches (${exX.length})`, x: exX, y: exY, marker: { size: 7, color: t.loss, line: { color: t.surface, width: 1 } }, hovertemplate: "%{x|%d %b %Y}  breach <b>%{y:.2%}</b><extra></extra>" } as Data,
      ];
    },
    [s, model],
  );
  const layout = useMemo(() => ({ hovermode: "closest", margin: { l: 16, r: 8, t: 30, b: 28 }, xaxis: { type: "date" }, yaxis: { tickformat: ".1%", side: "right", zeroline: true }, showlegend: true }), []);
  const card = d.scorecards[model];
  return (
    <>
      {card && (
        <Reading>
          {modelLabel(model)} was breached on {card.exceptions} of {card.T.toLocaleString()} days ({fmtPct(card.exception_rate, 2)}) against {fmtPct(1 - d.alpha, 0)} promised
          {card.kupiec.p_value != null && card.kupiec.p_value < 0.05 ? " — too many (or too few) to be chance" : " — consistent with its confidence level"}
          {card.christoffersen.p_value_ind != null && card.christoffersen.p_value_ind < 0.05 ? ", and the breaches cluster in time, so it reacts too slowly to new volatility." : "."}
        </Reading>
      )}
      <Chart data={data} layout={layout as any} height={340} />
    </>
  );
}

// =================================================================== GARCH

function GarchSection({ req }: { req: PortfolioIn }) {
  const [horizon, setHorizon] = useState<"1" | "10" | "21">("10");
  const [days, setDays] = useState<"63" | "126" | "252">("126");
  const [realized, setRealized] = useState(false);
  const body = useMemo(() => ({ ...req, alpha: 0.99, horizon: Number(horizon), forecast_days: Number(days) }), [req, horizon, days]);
  const q = useApiPost<GarchOut>("/risk/garch", body);
  return (
    <Section
      title="Volatility, now and next"
      description="Volatility clusters: calm follows calm and storms follow storms. GARCH models estimate today's volatility from the recent past and forecast how it will drift back to its long-run level."
      actions={
        <>
          <Field label="VaR horizon" inline>
            <SegmentedControl size="sm" ariaLabel="VaR horizon" options={[{ value: "1", label: "1d" }, { value: "10", label: "10d" }, { value: "21", label: "21d" }]} value={horizon} onChange={setHorizon} />
          </Field>
          <Field label="Forecast" inline>
            <SegmentedControl size="sm" ariaLabel="Forecast length" options={[{ value: "63", label: "3M" }, { value: "126", label: "6M" }, { value: "252", label: "1Y" }]} value={days} onChange={setDays} />
          </Field>
        </>
      }
    >
      <div className="grid-3">
        <Panel<GarchOut> span={2} title="Conditional volatility" subtitle="The models' estimate of annualized volatility on each day, next to the EWMA (RiskMetrics) estimate." info={INFO.m_gjr_garch} query={q} skeletonHeight={320} notes={[]} actions={<Toggle label="Realized |r|" checked={realized} onChange={setRealized} />}>
          {(d) => <CondVol d={d} realized={realized} />}
        </Panel>
        <Panel<GarchOut> title="Forecast term structure" subtitle="Expected volatility for each day ahead, converging to the long-run level (dotted)." info={INFO.term_structure} query={q} skeletonHeight={320} notes={[]}>
          {(d) => <TermStructure d={d} />}
        </Panel>
      </div>
      <Panel<GarchOut> title="Model fit" subtitle="Maximum-likelihood estimates (standard errors in grey) and the VaR/ES each model implies over the chosen horizon." query={q} skeletonHeight={260}>
        {(d) => <GarchParamsTable d={d} />}
      </Panel>
    </Section>
  );
}

function CondVol({ d, realized }: { d: GarchOut; realized: boolean }) {
  const series = useMemo(() => {
    const x = asDates(d.models.gjr.conditional_vol_annualized.index);
    const out = [
      { name: "GJR-GARCH", x, y: d.models.gjr.conditional_vol_annualized.values as (number | null)[], width: 1.8 },
      { name: "GARCH", x, y: d.models.garch.conditional_vol_annualized.values as (number | null)[], width: 1.2 },
      { name: "EWMA", x: asDates(d.ewma_vol_annualized.index), y: d.ewma_vol_annualized.values as (number | null)[], width: 1, dash: "dot" as const },
    ];
    return out;
  }, [d]);
  const extra = useMemo(() => {
    if (!realized) return undefined;
    return { yaxis: { rangemode: "tozero" } };
  }, [realized]);
  const cur = d.models.gjr.params;
  return (
    <>
      <Reading>
        Today's GJR-GARCH volatility is <strong className="num">{fmtPct(cur.next_day_vol * Math.sqrt(252), 1)}</strong> annualized, against a long-run level of <span className="num">{fmtPct(cur.unconditional_vol_annualized, 1)}</span>
        {cur.unconditional_vol_annualized != null ? (cur.next_day_vol * Math.sqrt(252) < cur.unconditional_vol_annualized ? " — calmer than usual, so expect it to drift up." : " — more turbulent than usual, so expect it to drift down.") : "."}
      </Reading>
      {realized ? <RealizedOverlay d={d} series={series} /> : <TimeSeriesChart series={series} yFormat="pct" digits={1} height={290} rangeSelector layout={extra as any} />}
    </>
  );
}

function RealizedOverlay({ d, series }: { d: GarchOut; series: { name: string; x: string[]; y: (number | null)[]; width?: number; dash?: "dot" }[] }) {
  const data = useMemo(
    () => (t: Tokens): Data[] => [
      { type: "scattergl", mode: "markers", name: "|r|·√252", x: asDates(d.realized_abs_return_annualized.index), y: d.realized_abs_return_annualized.values, marker: { size: 2.5, color: withAlpha(t.text3, 0.35) }, hovertemplate: "|r|·√252 %{y:.1%}<extra></extra>" } as Data,
      ...series.map((s, i) => ({ type: "scatter", mode: "lines", name: s.name, x: s.x, y: s.y, line: { color: t.categorical[i], width: s.width ?? 1.4, dash: s.dash }, hovertemplate: `<b>${s.name}</b> %{y:.1%}<extra></extra>` }) as Data),
    ],
    [d, series],
  );
  return <Chart data={data} layout={{ hovermode: "x unified", margin: { l: 16, r: 8, t: 30, b: 28 }, xaxis: { type: "date" }, yaxis: { tickformat: ".0%", side: "right", range: [0, 0.8] }, showlegend: true } as any} height={290} />;
}

function TermStructure({ d }: { d: GarchOut }) {
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const out: Data[] = [];
      (["gjr", "garch"] as const).forEach((k, i) => {
        const ts = d.models[k].term_structure;
        const y = (ts.data.daily_vol ?? []).map((v) => (v == null ? null : v * Math.sqrt(252)));
        out.push({ type: "scatter", mode: "lines", name: k === "gjr" ? "GJR-GARCH" : "GARCH", x: ts.index, y, line: { color: t.categorical[i], width: 2 - i * 0.6 }, hovertemplate: `<b>${k === "gjr" ? "GJR" : "GARCH"}</b> day %{x}: %{y:.1%}<extra></extra>` } as Data);
      });
      return out;
    },
    [d],
  );
  const layout = useMemo(
    () => (t: Tokens) => {
      const lr = (["gjr", "garch"] as const)
        .map((k, i) => ({ v: d.models[k].params.unconditional_vol_annualized, c: t.categorical[i] }))
        .filter((x): x is { v: number; c: string } => x.v != null && x.v < 1.5);
      return {
        hovermode: "x unified",
        margin: { l: 16, r: 8, t: 30, b: 36 },
        xaxis: { title: { text: "trading days ahead" }, showspikes: false },
        yaxis: { tickformat: ".0%", side: "right" },
        showlegend: true,
        shapes: lr.map((x) => ({ type: "line", xref: "paper", x0: 0, x1: 1, y0: x.v, y1: x.v, line: { color: x.c, width: 1, dash: "dot" } })),
      };
    },
    [d],
  );
  const g = d.models.garch.params;
  return (
    <>
      <Chart data={data} layout={layout as any} height={260} />
      {g.unconditional_vol_annualized != null && g.unconditional_vol_annualized >= 1.5 && <div className="subtle small">GARCH's long-run level ({fmtPct(g.unconditional_vol_annualized, 0)}) is off-scale: persistence is close to 1, so its forecast barely mean-reverts.</div>}
    </>
  );
}

function GarchParamsTable({ d }: { d: GarchOut }) {
  const g = d.models.garch;
  const j = d.models.gjr;
  type Row = { label: string; info?: Parameters<typeof KV>[0]["rows"][number]["info"]; garch: string; gjr: string; seG?: number; seJ?: number };
  const se = (m: typeof g, k: string) => m.params.std_err?.[k];
  const rows: Row[] = [
    { label: "ω", info: { text: "Constant in the variance equation (percent² per day)." }, garch: fmtNum(g.params.omega, 4), gjr: fmtNum(j.params.omega, 4), seG: se(g, "omega"), seJ: se(j, "omega") },
    { label: "α (ARCH)", info: { text: "Reaction of tomorrow's variance to today's squared shock." }, garch: fmtNum(g.params.alpha, 3), gjr: fmtNum(j.params.alpha, 3), seG: se(g, "alpha[1]"), seJ: se(j, "alpha[1]") },
    { label: "γ (leverage)", info: { text: "Extra reaction to negative shocks (GJR only). Positive = bad news raises volatility more than good news." }, garch: "—", gjr: fmtNum(j.params.gamma, 3), seJ: se(j, "gamma[1]") },
    { label: "β (GARCH)", info: { text: "Weight on yesterday's variance: how much volatility carries over." }, garch: fmtNum(g.params.beta, 3), gjr: fmtNum(j.params.beta, 3), seG: se(g, "beta[1]"), seJ: se(j, "beta[1]") },
    { label: "ν (Student-t dof)", info: { text: "Tail thickness of the shocks. Lower = fatter tails; above ~30 is essentially normal." }, garch: fmtNum(g.params.nu, 1), gjr: fmtNum(j.params.nu, 1), seG: se(g, "nu"), seJ: se(j, "nu") },
    { label: "Persistence", info: INFO.persistence, garch: fmtNum(g.params.persistence, 4), gjr: fmtNum(j.params.persistence, 4) },
    { label: "Half-life", info: INFO.persistence, garch: g.params.half_life_days != null ? `${fmtNum(g.params.half_life_days, 0)} d` : "—", gjr: j.params.half_life_days != null ? `${fmtNum(j.params.half_life_days, 0)} d` : "—" },
    { label: "Long-run vol", garch: fmtPct(g.params.unconditional_vol_annualized, 1), gjr: fmtPct(j.params.unconditional_vol_annualized, 1) },
    { label: "BIC", info: { text: "Bayesian information criterion: fit penalized for parameters. Lower is better." }, garch: fmtNum(d.comparison.bic.garch, 1), gjr: fmtNum(d.comparison.bic.gjr, 1) },
    { label: `VaR ${d.horizon}d 99%`, info: "var", garch: `${fmtPct(g.var_es_horizon.var, 2)} · ${usd(g.var_es_horizon.var_usd)}`, gjr: `${fmtPct(j.var_es_horizon.var, 2)} · ${usd(j.var_es_horizon.var_usd)}` },
    { label: `ES ${d.horizon}d 99%`, info: "es", garch: `${fmtPct(g.var_es_horizon.es, 2)} · ${usd(g.var_es_horizon.es_usd)}`, gjr: `${fmtPct(j.var_es_horizon.es, 2)} · ${usd(j.var_es_horizon.es_usd)}` },
  ];
  const best = d.comparison.preferred_by_bic;
  const cols: Column<Row>[] = [
    { key: "label", label: "Parameter", sortable: false, render: (r) => (<span className="pl-model"><span>{r.label}</span><InfoTip info={r.info} size={12} /></span>) },
    { key: "garch", label: <span>GARCH(1,1)-t {best === "garch" && <span className="badge accent">BIC best</span>}</span>, numeric: true, sortable: false, render: (r) => <span>{r.garch}{r.seG != null && <span className="pl-se">±{fmtNum(r.seG, 3)}</span>}</span> },
    { key: "gjr", label: <span>GJR-GARCH-t {best === "gjr" && <span className="badge accent">BIC best</span>}</span>, numeric: true, sortable: false, render: (r) => <span>{r.gjr}{r.seJ != null && <span className="pl-se">±{fmtNum(r.seJ, 3)}</span>}</span> },
  ];
  const lr = d.comparison;
  return (
    <div className="grid-3">
      <div className="span-2">
        <DataTable rows={rows} columns={cols} rowKey={(r) => r.label} />
      </div>
      <div className="stack">
        <Reading>
          {lr.lr_p_value < 0.05
            ? `The leverage term is significant (LR = ${fmtNum(lr.lr_gjr_vs_garch, 1)}, p ${lr.lr_p_value < 0.001 ? "< 0.001" : `= ${fmtNum(lr.lr_p_value, 3)}`}): losses raise this portfolio's future volatility more than gains of the same size.`
            : `The leverage term is not significant (LR = ${fmtNum(lr.lr_gjr_vs_garch, 1)}, p = ${fmtNum(lr.lr_p_value, 2)}): good and bad days move volatility about equally.`}
        </Reading>
        <KV
          rows={[
            { label: `FHS VaR ${d.horizon}d 99%`, value: `${fmtPct(d.fhs.var, 2)}`, hint: usd(d.fhs.var_usd), info: INFO.m_fhs },
            { label: `FHS ES ${d.horizon}d 99%`, value: `${fmtPct(d.fhs.es, 2)}`, hint: usd(d.fhs.es_usd), info: INFO.m_fhs },
            { label: "Next-day vol (GJR)", value: fmtPct(j.params.next_day_vol, 2), hint: "daily" },
          ]}
        />
      </div>
    </div>
  );
}
