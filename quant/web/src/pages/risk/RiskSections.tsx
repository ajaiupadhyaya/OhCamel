/**
 * Risk (/risk) on the active portfolio: VaR/ES across nine models (POST /risk/summary), Euler
 * split (/risk/decomposition), out-of-sample backtests (/risk/backtest, explicit RUN) and GARCH
 * (/risk/garch). Paper Tape: caps labels, ruled tables, readout lines instead of sentences;
 * signal only for losses, breaches and rejected tests.
 */
import { useMemo, useState } from "react";
import { BarChart, DataTable, HeatmapChart, InfoTip, Panel, Section, SegmentedControl, Select, StatGrid, StatTile, TimeSeriesChart, Toggle, type Column } from "../../components";
import { XYChart } from "../../charts/XYChart";
import { fmtMultiple, fmtNum, fmtPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import { frameToMatrix } from "../../lib/series";
import type { PortfolioIn } from "../../lib/types";
import { CONDITIONAL_MODELS, INFO, modelInfo, modelLabel } from "../portfolio/info";
import { KV, PValue, PctUsd, Readline, RunButton, ShareRows, ZoneChip, asDates, useCommitted, usd } from "../portfolio/shared";
import type { BacktestOut, DecompPosition, DecompositionOut, Estimate, GarchOut, RiskSummaryOut, Scorecard } from "../portfolio/types";
import { splitBreaches } from "./breaches";

type Alpha = "0.95" | "0.99";
const ALPHAS: { value: Alpha; label: string }[] = [
  { value: "0.95", label: "95" },
  { value: "0.99", label: "99" },
];
const lvl = (a: number | string) => fmtNum(Number(a) * 100, 0);
const MODEL = (m: string) => modelLabel(m).toUpperCase();

/** `alpha` and `horizon` seed the controls (the /risk?alpha=&h= link); they stay free after that. */
export function RiskSections({ req, alpha = "0.99", horizon = "1" }: { req: PortfolioIn; alpha?: Alpha; horizon?: "1" | "10" }) {
  return (
    <>
      <ModelsSection req={req} seedAlpha={alpha} seedHorizon={horizon} />
      <DecompositionSection req={req} seedAlpha={alpha} />
      <BacktestSection req={req} seedAlpha={alpha} />
      <GarchSection req={req} />
    </>
  );
}

// =================================================================== summary

function isInvalid(e: Estimate | undefined): boolean {
  return !!e && (e.var == null || (e.params as { valid_domain?: boolean } | undefined)?.valid_domain === false);
}

function ModelsSection({ req, seedAlpha, seedHorizon }: { req: PortfolioIn; seedAlpha: Alpha; seedHorizon: "1" | "10" }) {
  const [horizon, setHorizon] = useState<"1" | "10">(seedHorizon);
  const [alpha, setAlpha] = useState<Alpha>(seedAlpha);
  const body = useMemo(() => ({ ...req, alphas: [0.99, 0.95], horizon: Number(horizon) }), [req, horizon]);
  const q = useApiPost<RiskSummaryOut>("/risk/summary", body);
  return (
    <Section
      title={`VaR · ES · ${horizon}D`}
      actions={<SegmentedControl size="sm" ariaLabel="Horizon" options={[{ value: "1", label: "1D" }, { value: "10", label: "10D" }]} value={horizon} onChange={setHorizon} />}
    >
      <Panel<RiskSummaryOut> title={`Models · ${horizon}D`} query={q} skeletonHeight={380} flush notes={[]}>
        {(d) => (
          <>
            <SummaryLine d={d} />
            <ModelTable d={d} />
          </>
        )}
      </Panel>
      <div className="grid-3">
        <Panel<RiskSummaryOut> span={2} title={`VaR ${lvl(alpha)} · by model`} query={q} skeletonHeight={300} actions={<SegmentedControl size="sm" ariaLabel="Confidence" options={ALPHAS} value={alpha} onChange={setAlpha} />}>
          {(d) => <ModelBars d={d} alpha={alpha} />}
        </Panel>
        <Panel<RiskSummaryOut> title="Returns · fit" query={q} skeletonHeight={300} notes={[]}>
          {(d) => <DistributionFit d={d} />}
        </Panel>
      </div>
    </Section>
  );
}

function SummaryLine({ d }: { d: RiskSummaryOut }) {
  const valid = d.estimates.filter((e) => Math.abs(e.alpha - 0.99) < 1e-9 && !isInvalid(e) && e.var != null) as (Estimate & { var: number })[];
  if (valid.length < 2) return <Readline items={[{ k: "VAR 99", v: "FEWER THAN 2 MODELS" }]} />;
  const lo = valid.reduce((a, b) => (b.var < a.var ? b : a));
  const hi = valid.reduce((a, b) => (b.var > a.var ? b : a));
  const mean = (pick: (e: Estimate) => boolean) => {
    const xs = valid.filter(pick).map((e) => e.var);
    return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  };
  const cond = mean((e) => CONDITIONAL_MODELS.includes(e.model));
  const uncond = mean((e) => !CONDITIONAL_MODELS.includes(e.model));
  return (
    <Readline
      items={[
        { k: "VAR 99 LO", v: `${fmtPct(lo.var, 2)} ${MODEL(lo.model)}` },
        { k: "HI", v: `${fmtPct(hi.var, 2)} ${MODEL(hi.model)}` },
        { k: "SPREAD", v: fmtMultiple(hi.var / lo.var, 2) },
        ...(cond != null && uncond != null ? [{ k: "COND / UNCOND", v: fmtPct(cond / uncond - 1, 0, { signed: true }) }] : []),
      ]}
    />
  );
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
    render: (r) => (r[k]?.[f] == null ? <span className="subtle" title={r[k]?.error}>N/A</span> : <PctUsd pct={r[k]![f]} usd={r[k]![`${f}_usd`]} />),
    info: f === "var" ? "var" : "es",
    hideBelow: k === "e95" ? 600 : undefined,
  });
  const cols: Column<ModelRow>[] = [
    {
      key: "model",
      label: "Model",
      render: (r) => (
        <span className="pl-model">
          <span className="pl-model-name">{MODEL(r.model)}</span>
          <InfoTip info={{ ...modelInfo(r.model), reference: r.e99?.reference ?? modelInfo(r.model).reference }} size={12} label={modelLabel(r.model)} />
          {(isInvalid(r.e99) || isInvalid(r.e95)) && (
            <span className="pl-tag" title={[...(r.e99?.notes ?? []), ...(r.e95?.notes ?? [])].join(" ")}>
              OUT OF DOMAIN
            </span>
          )}
        </span>
      ),
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
      info: { text: "Tail weight beyond VaR. About 1.15 for a normal at 99%." },
    },
  ];
  return <DataTable rows={rows} columns={cols} rowKey={(r) => r.model} compact />;
}

function ModelBars({ d, alpha }: { d: RiskSummaryOut; alpha: Alpha }) {
  const rows = d.estimates.filter((e) => Math.abs(e.alpha - Number(alpha)) < 1e-9 && e.var != null && !isInvalid(e)).sort((a, b) => (b.var ?? 0) - (a.var ?? 0));
  const out = d.estimates.filter((e) => Math.abs(e.alpha - Number(alpha)) < 1e-9 && isInvalid(e)).map((e) => MODEL(e.model));
  return (
    <>
      <BarChart horizontal x={rows.map((r) => MODEL(r.model))} y={rows.map((r) => r.var)} yFormat="pct" digits={2} height={rows.length * 26 + 12} />
      {out.length > 0 && <div className="pl-foot num">EXCLUDED · OUT OF DOMAIN · {out.join(" ")}</div>}
    </>
  );
}

function DistributionFit({ d }: { d: RiskSummaryOut }) {
  const dist = d.distribution;
  const s = d.stats;
  const series = useMemo(
    () => [
      { name: "DAYS", y: dist.counts, mode: "bars" as const, tone: "ink3" as const },
      { name: "NORMAL", y: dist.normal_expected, tone: "ink" as const },
      ...(dist.t_expected ? [{ name: "STUDENT-T", y: dist.t_expected, tone: "ink2" as const, dash: "dot" as const }] : []),
    ],
    [dist],
  );
  return (
    <>
      <XYChart x={dist.centers} series={series} xFormat="pct" yFormat="int" digits={0} logY yMin={0.5} height={220} ariaLabel="Daily return histogram with fitted normal and Student-t, log scale" />
      <KV
        rows={[
          { label: "Skew", value: fmtNum(s.skew, 2), info: INFO.skew },
          { label: "Excess kurt", value: fmtNum(s.excess_kurtosis, 1), info: INFO.kurtosis },
          { label: "Jarque–Bera", value: fmtNum(s.jarque_bera.stat, 0), info: INFO.jb, hint: s.jarque_bera.p_value < 0.001 ? "p <0.001" : `p ${fmtNum(s.jarque_bera.p_value, 3)}` },
          { label: "Vol ann", value: fmtPct(s.vol_annualized, 1), info: "vol" },
        ]}
      />
    </>
  );
}

// =================================================================== decomposition

function DecompositionSection({ req, seedAlpha }: { req: PortfolioIn; seedAlpha: Alpha }) {
  const [alpha, setAlpha] = useState<Alpha>(seedAlpha);
  const [cov, setCov] = useState<"sample" | "ewma">("sample");
  const body = useMemo(() => ({ ...req, alpha: Number(alpha), cov_method: cov }), [req, alpha, cov]);
  const q = useApiPost<DecompositionOut>("/risk/decomposition", body);
  return (
    <Section
      title={`Euler · VaR ${lvl(alpha)}`}
      actions={
        <>
          <SegmentedControl size="sm" ariaLabel="Confidence" options={ALPHAS} value={alpha} onChange={setAlpha} />
          <SegmentedControl size="sm" ariaLabel="Covariance" options={[{ value: "sample", label: "SAMPLE" }, { value: "ewma", label: "EWMA" }]} value={cov} onChange={setCov} />
        </>
      }
    >
      <Panel<DecompositionOut> query={q} skeletonHeight={110} notes={[]} compact>
        {(d) => <DecompHeadline d={d} />}
      </Panel>
      <div className="grid-2">
        <Panel<DecompositionOut> title="Capital · VaR share" info={INFO.component_var} query={q} skeletonHeight={280} notes={[]} flush>
          {(d) => <ShareRows rows={d.positions.map((p) => ({ ticker: p.ticker, weight: p.weight, share: p.pct_var }))} riskLabel={`VAR ${lvl(d.alpha)}`} />}
        </Panel>
        <Panel<DecompositionOut> title="Correlation" info={INFO.correlation} query={q} skeletonHeight={320} notes={[]}>
          {(d) => {
            const m = frameToMatrix(d.correlation);
            return <HeatmapChart x={m.x} y={m.y} z={m.z} diverging palette="neutral" zmin={-1} zmax={1} format="num" digits={2} showValues={m.x.length <= 10} height={320} />;
          }}
        </Panel>
      </div>
      <Panel<DecompositionOut> title="Positions" query={q} flush skeletonHeight={260}>
        {(d) => <DecompTable d={d} />}
      </Panel>
    </Section>
  );
}

function DecompHeadline({ d }: { d: DecompositionOut }) {
  const t = d.totals;
  const b = d.benchmark;
  const a = lvl(d.alpha);
  return (
    <StatGrid min={140}>
      <StatTile label={`VaR ${a} · Param`} value={fmtPct(t.parametric_var, 2)} caption={<span className="num">{usd(t.parametric_var_usd)}</span>} info={INFO.m_gaussian} />
      <StatTile label={`VaR ${a} · Hist`} value={fmtPct(t.historical_var, 2)} caption={<span className="num">{usd(t.historical_var_usd)}</span>} info={INFO.m_historical} />
      <StatTile label={`ES ${a} · Hist`} value={fmtPct(t.historical_es, 2)} caption={<span className="num">{usd(t.historical_es_usd)}</span>} info="es" />
      <StatTile label="Div ratio" value={fmtMultiple(t.diversification_ratio, 2)} info={INFO.diversification_ratio} />
      <StatTile label="Div benefit" value={usd(t.diversification_benefit_var_usd)} caption={<span className="num">{fmtPct(t.diversification_benefit_var / t.sum_standalone_var, 0)} OF STAND-ALONE</span>} info={{ text: "Sum of stand-alone VaRs minus portfolio VaR." }} />
      {b && <StatTile label={`Beta · ${b.benchmark}`} value={fmtNum(b.portfolio_beta, 2)} info="beta" caption={<span className="num">TE {fmtPct(b.tracking_error_annualized, 1)}</span>} />}
    </StatGrid>
  );
}

function DecompTable({ d }: { d: DecompositionOut }) {
  const beta = d.benchmark?.position_beta ?? {};
  const cols: Column<DecompPosition>[] = [
    { key: "ticker", label: "Holding", render: (r) => <span className="num">{r.ticker}</span> },
    { key: "weight", label: "Weight", numeric: true, format: (v) => fmtPct(v, 1) },
    { key: "vol_annualized", label: "Vol", numeric: true, format: (v) => fmtPct(v, 1), info: "vol", hideBelow: 900 },
    { key: "beta", label: "Beta", numeric: true, value: (r) => beta[r.ticker] ?? null, format: (v) => fmtNum(v, 2), info: "beta", hideBelow: 1200 },
    { key: "marginal_var", label: "Marg VaR", numeric: true, format: (v) => fmtPct(v, 2), info: INFO.marginal_var, hideBelow: 900 },
    { key: "component_var", label: "Comp VaR", numeric: true, render: (r) => <PctUsd pct={r.component_var} usd={r.component_var_usd} />, value: (r) => r.component_var, info: INFO.component_var },
    { key: "pct_var", label: "% VaR", numeric: true, format: (v) => fmtPct(v, 1), heat: { min: 0, max: Math.max(0.3, ...d.positions.map((p) => p.pct_var)) } },
    { key: "hist_component_es", label: "Hist ES contrib", numeric: true, render: (r) => <PctUsd pct={r.hist_component_es} usd={r.hist_component_es_usd} />, value: (r) => r.hist_component_es, info: { text: "Average loss on the portfolio's worst (1 − α) days; sums to historical ES." }, hideBelow: 600 },
    { key: "incremental_var", label: "Incr VaR", numeric: true, render: (r) => <PctUsd pct={r.incremental_var} usd={r.incremental_var_usd} />, value: (r) => r.incremental_var, info: INFO.incremental_var, hideBelow: 1200 },
  ];
  return <DataTable rows={d.positions} columns={cols} rowKey={(r) => r.ticker} defaultSort={{ key: "pct_var", dir: "desc" }} compact />;
}

// =================================================================== backtest

const WINDOWS = ["250", "500", "1000"] as const;

function BacktestSection({ req, seedAlpha }: { req: PortfolioIn; seedAlpha: Alpha }) {
  const [alpha, setAlpha] = useState<Alpha>(seedAlpha);
  const [window, setWindow] = useState<(typeof WINDOWS)[number]>("500");
  const [refit, setRefit] = useState<"5" | "20" | "60">("20");
  const draft = useMemo(() => ({ ...req, alpha: Number(alpha), window: Number(window), refit_every: Number(refit) }), [req, alpha, window, refit]);
  const { committed, run, dirty } = useCommitted(draft);
  const q = useApiPost<BacktestOut>("/risk/backtest", committed);
  const [model, setModel] = useState<string | null>(null);
  const shown = model ?? q.data?.ranking_by_fz0[0] ?? "historical";
  const n = q.data?.series.dates.length;
  const asOf = q.data?.series.dates[q.data.series.dates.length - 1];
  return (
    <Section
      title={`Backtest · VaR ${lvl(alpha)}`}
      actions={
        <div className="pl-controls">
          <SegmentedControl size="sm" ariaLabel="Confidence" options={ALPHAS} value={alpha} onChange={setAlpha} />
          <SegmentedControl size="sm" ariaLabel="Estimation window" options={WINDOWS.map((w) => ({ value: w, label: `W${w}` }))} value={window} onChange={setWindow} />
          <SegmentedControl size="sm" ariaLabel="Refit frequency" options={[{ value: "5", label: "R5" }, { value: "20", label: "R20" }, { value: "60", label: "R60" }]} value={refit} onChange={setRefit} />
          <RunButton onRun={run} dirty={dirty} busy={q.isFetching} />
        </div>
      }
    >
      <Panel<BacktestOut>
        title={`Scorecard${n ? ` · ${fmtNum(n, 0)} OOS days` : ""}`}
        query={q}
        flush
        skeletonHeight={360}
        asOf={asOf}
        actions={q.data ? <span className="pl-meta num">FZ0 RANK · {fmtNum(q.data.compute_seconds, 1)}S</span> : undefined}
      >
        {(d) => <Scorecards d={d} active={shown} onPick={setModel} />}
      </Panel>
      <Panel<BacktestOut>
        title={`P&L · −VaR · ${MODEL(shown)}`}
        query={q}
        skeletonHeight={360}
        notes={[]}
        asOf={asOf}
        actions={q.data ? <Select ariaLabel="Model" value={shown} onChange={setModel} options={q.data.ranking_by_fz0.map((m) => ({ value: m, label: MODEL(m) }))} /> : undefined}
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
    { key: "model", label: "Model", render: (r) => <span className="pl-model-name">{MODEL(r.model)}</span> },
    {
      key: "exceptions",
      label: "Breaches",
      numeric: true,
      render: (r) => (
        <span>
          {r.exceptions}
          <span className="subtle"> / {fmtNum(r.expected, 0)}</span>
        </span>
      ),
      info: { text: "Days the loss exceeded that morning's VaR / the number promised." },
    },
    { key: "exception_rate", label: "Rate", numeric: true, format: (v) => fmtPct(v, 2), hideBelow: 900 },
    { key: "kupiec", label: "Kupiec", numeric: true, value: (r) => r.kupiec.p_value, render: (r) => <PValue p={r.kupiec.p_value} />, info: INFO.kupiec },
    { key: "ind", label: "Indep", numeric: true, value: (r) => r.christoffersen.p_value_ind, render: (r) => <PValue p={r.christoffersen.p_value_ind} />, info: INFO.christoffersen, hideBelow: 600 },
    { key: "cc", label: "CC", numeric: true, value: (r) => r.christoffersen.p_value_cc, render: (r) => <PValue p={r.christoffersen.p_value_cc} />, info: INFO.cc, hideBelow: 900 },
    { key: "dq", label: "DQ", numeric: true, value: (r) => r.dq?.p_value ?? null, render: (r) => <PValue p={r.dq?.p_value} />, info: INFO.dq, hideBelow: 900 },
    {
      key: "zone",
      label: "Basel",
      value: (r) => ({ green: 0, yellow: 1, red: 2 })[r.traffic_light.zone],
      render: (r) => <ZoneChip zone={r.traffic_light.zone} title={`${r.traffic_light.exceptions} breaches in ${r.traffic_light.T} days; green ≤ ${r.traffic_light.green_max}, yellow ≤ ${r.traffic_light.yellow_max}`} />,
      info: INFO.traffic_light,
    },
    ...(is99
      ? [
          {
            key: "basel250",
            label: "Last 250",
            value: (r: CardRow) => r.basel_last_250?.exceptions ?? null,
            render: (r: CardRow) =>
              r.basel_last_250 ? (
                <span className="pl-basel">
                  <ZoneChip zone={r.basel_last_250.zone} title={`${r.basel_last_250.exceptions} breaches in the last ${r.basel_last_250.T} days`} />
                  <span className="subtle num">×{fmtNum(r.basel_last_250.multiplier, 2)}</span>
                </span>
              ) : (
                "—"
              ),
            info: { text: "Breaches in the last 250 days and the Basel capital multiplier.", reference: "BCBS (1996)" },
            hideBelow: 1200,
          } as Column<CardRow>,
        ]
      : []),
    {
      key: "z2",
      label: "ES test",
      value: (r) => r.acerbi_szekely_z2?.z2 ?? null,
      render: (r) => {
        const z = r.acerbi_szekely_z2;
        if (!z) return "—";
        const word = z.indicative_verdict.split(" ")[0].toUpperCase();
        return (
          <span className={`num ${z.indicative_verdict === "reject" ? "loss" : z.indicative_verdict === "accept" ? "" : "subtle"}`} title={`Z₂ = ${fmtNum(z.z2, 2)} (5% critical ${fmtNum(z.critical_5pct, 1)})`}>
            {word}
          </span>
        );
      },
      info: INFO.z2,
      hideBelow: 600,
    },
  ];
  return <DataTable rows={rows} columns={cols} rowKey={(r) => r.model} onRowClick={(r) => onPick(r.model)} isActive={(r) => r.model === active} compact />;
}

function BacktestChart({ d, model }: { d: BacktestOut; model: string }) {
  const s = d.series;
  const { series, count } = useMemo(() => {
    const v = s.var[model] ?? [];
    const e = s.es[model] ?? [];
    const split = splitBreaches(s.returns, v);
    return {
      count: split.count,
      series: [
        { name: "RET", y: split.ok, mode: "points" as const, tone: "ink3" as const, size: 2 },
        { name: "−VAR", y: v.map((x) => (x == null ? null : -x)), tone: "ink" as const },
        { name: "−ES", y: e.map((x) => (x == null ? null : -x)), tone: "ink2" as const, dash: "dot" as const },
        { name: "BREACH", y: split.breach, mode: "points" as const, tone: "signal" as const, size: 5 },
      ],
    };
  }, [s, model]);
  const card = d.scorecards[model];
  const rejected = card?.kupiec.p_value != null && card.kupiec.p_value < 0.05;
  const clustered = card?.christoffersen.p_value_ind != null && card.christoffersen.p_value_ind < 0.05;
  return (
    <>
      {card && (
        <Readline
          items={[
            { k: "BREACHES", v: `${card.exceptions} / ${fmtNum(card.T, 0)}`, tone: rejected ? "loss" : "" },
            { k: "RATE", v: fmtPct(card.exception_rate, 2) },
            { k: "PROMISED", v: fmtPct(1 - d.alpha, 0) },
            { k: "KUPIEC P", v: card.kupiec.p_value == null ? "—" : fmtNum(card.kupiec.p_value, 3), tone: rejected ? "loss" : "" },
            { k: "CLUSTERED", v: clustered ? "YES" : "NO", tone: clustered ? "loss" : "" },
          ]}
        />
      )}
      <XYChart time x={s.dates} series={series} yFormat="pct" digits={2} height={340} zero ariaLabel={`Daily portfolio returns against ${modelLabel(model)} VaR and ES, ${count} breaches`} />
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
  const idx = q.data?.models.gjr.conditional_vol_annualized.index;
  const asOf = idx && idx.length ? String(idx[idx.length - 1]) : undefined;
  return (
    <Section
      title="GARCH · t"
      actions={
        <>
          <SegmentedControl size="sm" ariaLabel="VaR horizon" options={[{ value: "1", label: "1D" }, { value: "10", label: "10D" }, { value: "21", label: "21D" }]} value={horizon} onChange={setHorizon} />
          <SegmentedControl size="sm" ariaLabel="Forecast length" options={[{ value: "63", label: "3M" }, { value: "126", label: "6M" }, { value: "252", label: "1Y" }]} value={days} onChange={setDays} />
        </>
      }
    >
      <div className="grid-3">
        <Panel<GarchOut> span={2} title="Cond vol · ann" query={q} skeletonHeight={320} notes={[]} asOf={asOf} actions={<Toggle label="|R|" checked={realized} onChange={setRealized} />}>
          {(d) => <CondVol d={d} realized={realized} />}
        </Panel>
        <Panel<GarchOut> title="Vol forecast · ann" info={INFO.term_structure} query={q} skeletonHeight={320} notes={[]} asOf={asOf}>
          {(d) => <TermStructure d={d} />}
        </Panel>
      </div>
      <Panel<GarchOut> title="Fit · MLE" query={q} skeletonHeight={260} asOf={asOf}>
        {(d) => <GarchParamsTable d={d} />}
      </Panel>
    </Section>
  );
}

function CondVol({ d, realized }: { d: GarchOut; realized: boolean }) {
  const lines = useMemo(() => {
    const x = asDates(d.models.gjr.conditional_vol_annualized.index);
    return [
      { name: "GJR", x, y: d.models.gjr.conditional_vol_annualized.values as (number | null)[], color: "var(--ink)", width: 1.5 },
      { name: "GARCH", x, y: d.models.garch.conditional_vol_annualized.values as (number | null)[], color: "var(--ink-2)" },
      { name: "EWMA", x: asDates(d.ewma_vol_annualized.index), y: d.ewma_vol_annualized.values as (number | null)[], color: "var(--ink-2)", dash: "dot" as const },
    ];
  }, [d]);
  const overlay = useMemo(() => {
    if (!realized) return null;
    const x = lines[0].x;
    const rDates = asDates(d.realized_abs_return_annualized.index);
    const rmap = new Map(rDates.map((k, i) => [k, d.realized_abs_return_annualized.values[i] as number | null]));
    return {
      x,
      series: [
        { name: "|R|·√252", y: x.map((k) => rmap.get(k) ?? null), mode: "points" as const, tone: "ink3" as const, size: 2 },
        { name: "GJR", y: lines[0].y, tone: "ink" as const, width: 1.5 },
        { name: "GARCH", y: lines[1].y, tone: "ink2" as const },
      ],
    };
  }, [d, realized, lines]);
  const cur = d.models.gjr.params;
  const now = cur.next_day_vol * Math.sqrt(252);
  return (
    <>
      <Readline
        items={[
          { k: "GJR NOW", v: fmtPct(now, 1) },
          { k: "LONG RUN", v: fmtPct(cur.unconditional_vol_annualized, 1) },
          ...(cur.unconditional_vol_annualized != null ? [{ k: "GAP", v: fmtPct(now - cur.unconditional_vol_annualized, 1, { signed: true }) }] : []),
        ]}
      />
      {overlay ? (
        <XYChart time x={overlay.x} series={overlay.series} yFormat="pct" digits={1} height={290} zero ariaLabel="Conditional volatility against realized absolute returns" />
      ) : (
        <TimeSeriesChart series={lines} yFormat="pct" digits={1} height={290} />
      )}
    </>
  );
}

function TermStructure({ d }: { d: GarchOut }) {
  const x = d.models.gjr.term_structure.index as number[];
  const ann = (k: "gjr" | "garch") => (d.models[k].term_structure.data.daily_vol ?? []).map((v) => (v == null ? null : v * Math.sqrt(252)));
  const lr = d.models.gjr.params.unconditional_vol_annualized;
  const g = d.models.garch.params.unconditional_vol_annualized;
  return (
    <>
      <XYChart
        x={x}
        series={[
          { name: "GJR", y: ann("gjr"), tone: "ink", width: 1.5 },
          { name: "GARCH", y: ann("garch"), tone: "ink2" },
        ]}
        hlines={lr != null && lr < 1.5 ? [{ at: lr, label: "LR GJR" }] : []}
        xFormat="int"
        yFormat="pct"
        digits={1}
        xTitle="Days ahead"
        height={260}
        ariaLabel="GARCH volatility forecast by trading days ahead"
      />
      {g != null && g >= 1.5 && <div className="pl-foot num">GARCH LONG RUN {fmtPct(g, 0)} · OFF SCALE</div>}
    </>
  );
}

function GarchParamsTable({ d }: { d: GarchOut }) {
  const g = d.models.garch;
  const j = d.models.gjr;
  type Row = { label: string; info?: Parameters<typeof KV>[0]["rows"][number]["info"]; garch: string; gjr: string; seG?: number; seJ?: number };
  const se = (m: typeof g, k: string) => m.params.std_err?.[k];
  const rows: Row[] = [
    { label: "ω", garch: fmtNum(g.params.omega, 4), gjr: fmtNum(j.params.omega, 4), seG: se(g, "omega"), seJ: se(j, "omega") },
    { label: "α", garch: fmtNum(g.params.alpha, 3), gjr: fmtNum(j.params.alpha, 3), seG: se(g, "alpha[1]"), seJ: se(j, "alpha[1]") },
    { label: "γ", info: { text: "Leverage: extra variance response to negative shocks (GJR only)." }, garch: "—", gjr: fmtNum(j.params.gamma, 3), seJ: se(j, "gamma[1]") },
    { label: "β", garch: fmtNum(g.params.beta, 3), gjr: fmtNum(j.params.beta, 3), seG: se(g, "beta[1]"), seJ: se(j, "beta[1]") },
    { label: "ν", info: { text: "Student-t degrees of freedom; lower is fatter-tailed." }, garch: fmtNum(g.params.nu, 1), gjr: fmtNum(j.params.nu, 1), seG: se(g, "nu"), seJ: se(j, "nu") },
    { label: "Persistence", info: INFO.persistence, garch: fmtNum(g.params.persistence, 4), gjr: fmtNum(j.params.persistence, 4) },
    { label: "Half-life", info: INFO.persistence, garch: g.params.half_life_days != null ? `${fmtNum(g.params.half_life_days, 0)}D` : "—", gjr: j.params.half_life_days != null ? `${fmtNum(j.params.half_life_days, 0)}D` : "—" },
    { label: "Long-run vol", garch: fmtPct(g.params.unconditional_vol_annualized, 1), gjr: fmtPct(j.params.unconditional_vol_annualized, 1) },
    { label: "BIC", info: { text: "Bayesian information criterion; lower is better." }, garch: fmtNum(d.comparison.bic.garch, 1), gjr: fmtNum(d.comparison.bic.gjr, 1) },
    { label: `VaR 99 · ${d.horizon}D`, info: "var", garch: `${fmtPct(g.var_es_horizon.var, 2)} · ${usd(g.var_es_horizon.var_usd)}`, gjr: `${fmtPct(j.var_es_horizon.var, 2)} · ${usd(j.var_es_horizon.var_usd)}` },
    { label: `ES 99 · ${d.horizon}D`, info: "es", garch: `${fmtPct(g.var_es_horizon.es, 2)} · ${usd(g.var_es_horizon.es_usd)}`, gjr: `${fmtPct(j.var_es_horizon.es, 2)} · ${usd(j.var_es_horizon.es_usd)}` },
  ];
  const best = d.comparison.preferred_by_bic;
  const cols: Column<Row>[] = [
    { key: "label", label: "Param", sortable: false, render: (r) => (<span className="pl-model"><span>{r.label}</span><InfoTip info={r.info} size={12} label={r.label} /></span>) },
    { key: "garch", label: <span>GARCH(1,1)-t{best === "garch" && <span className="pl-tag">BIC</span>}</span>, numeric: true, sortable: false, render: (r) => <span>{r.garch}{r.seG != null && <span className="pl-se">±{fmtNum(r.seG, 3)}</span>}</span> },
    { key: "gjr", label: <span>GJR-GARCH-t{best === "gjr" && <span className="pl-tag">BIC</span>}</span>, numeric: true, sortable: false, render: (r) => <span>{r.gjr}{r.seJ != null && <span className="pl-se">±{fmtNum(r.seJ, 3)}</span>}</span> },
  ];
  const lr = d.comparison;
  return (
    <div className="grid-3">
      <div className="span-2">
        <DataTable rows={rows} columns={cols} rowKey={(r) => r.label} compact />
      </div>
      <KV
        rows={[
          { label: "LR · GJR vs GARCH", value: fmtNum(lr.lr_gjr_vs_garch, 1), hint: lr.lr_p_value < 0.001 ? "p <0.001" : `p ${fmtNum(lr.lr_p_value, 3)}`, info: { text: "Likelihood-ratio test of the leverage term γ = 0." } },
          { label: `FHS VaR 99 · ${d.horizon}D`, value: fmtPct(d.fhs.var, 2), hint: usd(d.fhs.var_usd), info: INFO.m_fhs },
          { label: `FHS ES 99 · ${d.horizon}D`, value: fmtPct(d.fhs.es, 2), hint: usd(d.fhs.es_usd), info: INFO.m_fhs },
          { label: "Next-day vol · GJR", value: fmtPct(j.params.next_day_vol, 2), hint: "DAILY" },
        ]}
      />
    </div>
  );
}
