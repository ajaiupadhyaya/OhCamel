/**
 * Factors — what the portfolio is really exposed to.
 *   GET  /api/factors/models      model catalogue + ETF preset
 *   POST /api/factors/regression  Fama–French / Carhart on Ken French's daily factors
 *   POST /api/factors/custom      the same on tradable long/short ETF spreads (works offline)
 * When the French library is unreachable (503) the page says so and offers the ETF model.
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, useTabParam, Icon, InfoTip, Panel, Section, SegmentedControl, Select, StatGrid, StatTile, TimeSeriesChart, withAlpha, type Column } from "../../components";
import { Bars } from "./Bars";
import { DataUnavailableError } from "../../lib/api";
import { fmtDate, fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import { useApiPost, useApiQuery } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import type { PortfolioIn } from "../../lib/types";
import { INFO } from "./info";
import { KV, Reading, RunButton, asDates, useCommitted } from "./shared";
import type { FactorDef, FactorModelsOut, FactorOut, Loading, Rebalance } from "./types";

type Source = "french" | "etf";

/** Default tradable factors, all built from ETFs in the committed offline dataset. */
export const DEFAULT_ETF_FACTORS: FactorDef[] = [
  { name: "MKT", long: "SPY", short: null },
  { name: "SIZE", long: "IWM", short: "SPY" },
  { name: "DURATION", long: "TLT", short: "IEF" },
  { name: "TECH", long: "QQQ", short: "SPY" },
];

export function FactorsTab({ req }: { req: PortfolioIn }) {
  const [source, setSource] = useTabParam<Source>("fsrc", "french");
  const [model, setModel] = useState("ff5");
  const [rebalance, setRebalance] = useState<Rebalance>("monthly");
  const models = useApiQuery<FactorModelsOut>("/factors/models", undefined, { staleTime: Infinity });
  const target = useMemo(() => ({ holdings: req.holdings, start: req.start, end: req.end, rebalance }), [req, rebalance]);
  const french = useApiPost<FactorOut>("/factors/regression", { ...target, model }, { enabled: source === "french" });
  const frenchDown = french.error instanceof DataUnavailableError;

  const [defs, setDefs] = useState<FactorDef[]>(DEFAULT_ETF_FACTORS);
  const [serverPreset, setServerPreset] = useState(false);
  const draft = useMemo(() => ({ ...target, factors: serverPreset ? null : defs.filter((d) => d.name.trim() && d.long.trim()).map((d) => ({ name: d.name.trim(), long: d.long.trim().toUpperCase(), short: d.short?.trim() ? d.short.trim().toUpperCase() : null })) }), [target, defs, serverPreset]);
  const { committed, run, dirty } = useCommitted(draft);
  const custom = useApiPost<FactorOut>("/factors/custom", committed, { enabled: source === "etf" });

  const q = source === "french" ? french : custom;
  const spec = models.data?.models.find((m) => m.key === model);

  return (
    <>
      <Section
        title="What drives the returns?"
        description="A factor regression explains daily excess returns with a few systematic return streams — the market, size, value, momentum, rates. The loadings are the portfolio's true exposures; what is left over is alpha and idiosyncratic noise."
        actions={
          <div className="pl-controls">
            <SegmentedControl
              size="sm"
              ariaLabel="Factor source"
              options={[
                { value: "french", label: "Fama–French", title: "Academic long/short factors from Kenneth French's data library" },
                { value: "etf", label: "Tradable ETFs", title: "Long/short spreads of real ETF returns you can build yourself" },
              ]}
              value={source}
              onChange={setSource}
            />
            {source === "french" && models.data && <Select ariaLabel="Factor model" value={model} onChange={setModel} options={models.data.models.map((m) => ({ value: m.key, label: m.label }))} />}
            <SegmentedControl size="sm" ariaLabel="Rebalancing" options={[{ value: "daily", label: "Daily" }, { value: "monthly", label: "Monthly" }, { value: "none", label: "Buy & hold" }]} value={rebalance} onChange={setRebalance} />
          </div>
        }
      >
        {source === "french" && spec && (
          <div className="pl-model-spec">
            <span className="eyebrow">{spec.label}</span>
            <span className="pl-factor-list">
              {spec.factors.map((f) => (
                <span key={f} className="badge" title={models.data?.factor_descriptions[f]}>
                  {f}
                </span>
              ))}
            </span>
            <span className="subtle small">{spec.reference}</span>
          </div>
        )}

        {source === "french" && frenchDown && (
          <div className="grid-3">
            <Panel title="Fama–French factors" subtitle="Daily research factors from Kenneth R. French's data library at Dartmouth." error={french.error} onRetry={() => french.refetch()} span={2} />
            <div className="pl-offer">
              <div className="pl-offer-icon">
                <Icon name="scale" size={20} />
              </div>
              <h4 className="display">Use tradable ETF factors instead</h4>
              <p>
                The same regression on long/short spreads of real ETF prices — market, small-minus-large, long-minus-intermediate Treasuries, tech-minus-market. They proxy the academic factors, can be traded, and you can edit every leg.
              </p>
              <button type="button" className="btn btn-primary" onClick={() => setSource("etf")}>
                Switch to ETF factors <Icon name="arrow-right" size={14} />
              </button>
            </div>
          </div>
        )}

        {source === "etf" && <FactorEditor defs={defs} onChange={(d) => (setServerPreset(false), setDefs(d))} onRun={run} dirty={dirty} busy={custom.isFetching} preset={models.data?.etf_preset} serverPreset={serverPreset} onServerPreset={() => setServerPreset(true)} />}

        {!(source === "french" && frenchDown) && <FactorResults q={q} />}
      </Section>
    </>
  );
}

// ------------------------------------------------------------------ custom factor editor

function FactorEditor({ defs, onChange, onRun, dirty, busy, preset, serverPreset, onServerPreset }: { defs: FactorDef[]; onChange: (d: FactorDef[]) => void; onRun: () => void; dirty: boolean; busy: boolean; preset?: FactorModelsOut["etf_preset"]; serverPreset: boolean; onServerPreset: () => void }) {
  if (serverPreset && preset)
    return (
      <Panel
        title="Tradable factor definitions · server preset"
        subtitle="The server's seven-factor ETF preset. Factors whose ETFs cannot be served here are dropped automatically and listed in the notes."
        actions={<RunButton onRun={onRun} dirty={dirty} busy={busy} label="Run regression" />}
      >
        <div className="pl-preset-list">
          {preset.map((p) => (
            <div key={p.name} className="pl-preset">
              <strong className="num">{p.name}</strong>
              <span className="num">
                {p.long} − {p.short ?? "T-bill"}
              </span>
              <span className="subtle small">{p.label}</span>
            </div>
          ))}
        </div>
        <div className="row-wrap" style={{ marginTop: 12 }}>
          <button type="button" className="btn btn-sm" onClick={() => onChange(DEFAULT_ETF_FACTORS)}>
            Edit my own factors
          </button>
        </div>
      </Panel>
    );
  const set = (i: number, patch: Partial<FactorDef>) => onChange(defs.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  return (
    <Panel
      title="Tradable factor definitions"
      subtitle="Each factor is the daily return of a long ETF minus a short ETF (or minus the T-bill when the short leg is empty). Edit a leg, add a factor, then run."
      info={{ text: "Tradable factors are zero-investment spreads you could actually hold. They proxy the academic factors but differ in universe, weighting and fees.", reference: "Huij & Verbeek (2009), Journal of Financial and Quantitative Analysis 44(1)" }}
      actions={<RunButton onRun={onRun} dirty={dirty} busy={busy} label="Run regression" />}
    >
      <div className="pl-factor-defs">
        <div className="pl-fd-row pl-fd-head">
          <span>Name</span>
          <span>Long</span>
          <span />
          <span>Short</span>
          <span />
        </div>
        {defs.map((d, i) => (
          <div className="pl-fd-row" key={i}>
            <input className="input num" aria-label="Factor name" value={d.name} maxLength={24} onChange={(e) => set(i, { name: e.target.value.toUpperCase() })} />
            <input className="input num" aria-label="Long leg" value={d.long} maxLength={12} onChange={(e) => set(i, { long: e.target.value.toUpperCase() })} />
            <span className="pl-fd-minus" aria-hidden>
              −
            </span>
            <input className="input num" aria-label="Short leg" placeholder="T-bill" value={d.short ?? ""} maxLength={12} onChange={(e) => set(i, { short: e.target.value.toUpperCase() || null })} />
            <button type="button" className="icon-btn" aria-label={`Remove ${d.name}`} onClick={() => onChange(defs.filter((_, j) => j !== i))} disabled={defs.length <= 1}>
              <Icon name="x" size={14} />
            </button>
          </div>
        ))}
      </div>
      <div className="row-wrap" style={{ marginTop: 12 }}>
        <button type="button" className="btn btn-sm" onClick={() => onChange([...defs, { name: `F${defs.length + 1}`, long: "", short: "SPY" }])} disabled={defs.length >= 10}>
          <Icon name="plus" size={14} /> Add factor
        </button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={() => onChange(DEFAULT_ETF_FACTORS)}>
          Reset to defaults
        </button>
        {preset && (
          <button type="button" className="btn btn-sm btn-ghost" title="Value, momentum, quality, credit… ETFs that cannot be served are dropped automatically" onClick={onServerPreset}>
            Use the server's {preset.length}-factor preset
          </button>
        )}
      </div>
    </Panel>
  );
}

// ------------------------------------------------------------------ results

type Q = ReturnType<typeof useApiPost<FactorOut>>;

function FactorResults({ q }: { q: Q }) {
  return (
    <>
      <Panel<FactorOut> query={q} skeletonHeight={110} compact notes={[]}>
        {(d) => <FactorHeadline d={d} />}
      </Panel>
      <div className="grid-2">
        <Panel<FactorOut> title="Factor loadings" subtitle="Point estimate with a 95% confidence interval (Newey–West). An interval that crosses zero is an exposure you cannot distinguish from none." info={INFO.nw_t} query={q} skeletonHeight={300} notes={[]}>
          {(d) => <CoefPlot d={d} />}
        </Panel>
        <Panel<FactorOut> title="Where the variance comes from" subtitle="Share of total return variance from each factor (Euler contributions) and the idiosyncratic remainder. Negative shares are hedges." info={INFO.factor_risk} query={q} skeletonHeight={300} notes={[]}>
          {(d) => <RiskShares d={d} />}
        </Panel>
      </div>
      <Panel<FactorOut> title="Cumulative return attribution" subtitle="How the compounded excess return splits into each factor's contribution, alpha and residual. The stacked layers always add to the total line." info={INFO.carino} query={q} skeletonHeight={340} notes={[]}>
        {(d) => <Attribution d={d} />}
      </Panel>
      <div className="grid-2">
        <Panel<FactorOut> title="Rolling exposures" subtitle={q.data ? `Loadings re-estimated on a trailing ${q.data.rolling_window}-day window. Drifting lines mean the portfolio's character changed.` : undefined} query={q} skeletonHeight={300} notes={[]} info={{ text: "OLS loadings on a moving window. Useful to see whether an exposure is stable or regime-dependent." }}>
          {(d) => <Rolling d={d} />}
        </Panel>
        <Panel<FactorOut> title="Loading detail" query={q} flush skeletonHeight={300}>
          {(d) => <LoadingsTable d={d} />}
        </Panel>
      </div>
    </>
  );
}

function FactorHeadline({ d }: { d: FactorOut }) {
  const s = d.stats;
  const rd = d.risk_decomposition;
  return (
    <StatGrid min={140}>
      <StatTile size="lg" label="R²" value={fmtNum(s.r2, 2)} info={INFO.r2} caption={<span className="num">adj {fmtNum(s.adj_r2, 3)}</span>} />
      <StatTile size="lg" label="Alpha (ann.)" value={s.alpha_ann} format={(v) => fmtSignedPct(v, 2)} tone="auto" info={INFO.factor_alpha} caption={<span className="num">t = {fmtNum(s.alpha_t, 2)}{Math.abs(s.alpha_t) < 2 ? " · not significant" : ""}</span>} />
      <StatTile size="lg" label="Systematic share" value={fmtPct(rd.systematic_share, 0)} info={INFO.factor_risk} caption={<span className="num">{fmtPct(rd.systematic_vol_ann, 1)} of {fmtPct(rd.total_vol_ann, 1)} vol</span>} />
      <StatTile size="lg" label="Idiosyncratic vol" value={fmtPct(s.residual_vol_ann, 1)} info={{ text: "Annualized volatility of the regression residual: risk the factors don't explain." }} />
      <StatTile size="lg" label="Appraisal ratio" value={s.appraisal_ratio} format={(v) => fmtNum(v, 2)} tone="auto" info={INFO.appraisal} />
      <StatTile size="lg" label="Sample" value={s.n_obs.toLocaleString()} caption={`${fmtDate(s.start, "month")} – ${fmtDate(s.end, "month")} · NW lags ${s.nw_lags}`} info={{ text: "Daily observations in the regression and the Newey–West lag length (Bartlett kernel)." }} />
    </StatGrid>
  );
}

const factorsOf = (d: FactorOut) => d.loadings.filter((l) => l.term !== "alpha");

function CoefPlot({ d }: { d: FactorOut }) {
  const rows = factorsOf(d);
  const data = useMemo(
    () => (t: Tokens): Data[] => [
      {
        type: "scatter",
        mode: "text+markers",
        x: rows.map((r) => r.estimate),
        y: rows.map((r) => r.term),
        text: rows.map((r) => `t ${fmtNum(r.t_stat, 1)}`),
        textposition: "top center",
        cliponaxis: false,
        textfont: { family: t.fontMono, size: 10, color: t.text3 },
        error_x: { type: "data", symmetric: false, array: rows.map((r) => r.ci_upper - r.estimate), arrayminus: rows.map((r) => r.estimate - r.ci_lower), color: t.text2, thickness: 1.5, width: 6 },
        marker: { size: 10, color: rows.map((r) => (Math.abs(r.t_stat) >= 2 ? t.categorical[0] : t.surface)), line: { color: t.categorical[0], width: 1.6 } },
        hovertemplate: "<b>%{y}</b>  β = %{x:.3f}<br>95% CI %{customdata[0]:.3f} … %{customdata[1]:.3f}<br>t = %{customdata[2]:.2f}<extra></extra>",
        customdata: rows.map((r) => [r.ci_lower, r.ci_upper, r.t_stat]),
      } as Data,
    ],
    [rows],
  );
  const layout = useMemo(() => ({ margin: { l: 8, r: 16, t: 22, b: 30 }, xaxis: { zeroline: true, rangemode: "tozero", showspikes: false, title: { text: "loading (β)" } }, yaxis: { type: "category", autorange: "reversed", showgrid: false }, showlegend: false, hovermode: "closest" }), []);
  const strongest = [...rows].sort((a, b) => Math.abs(b.t_stat) - Math.abs(a.t_stat))[0];
  return (
    <>
      {strongest && (
        <Reading>
          The strongest exposure is {strongest.term} (β = {fmtNum(strongest.estimate, 2)}, t = {fmtNum(strongest.t_stat, 1)}): a 1% move in that factor moves the portfolio about {fmtPct(Math.abs(strongest.estimate) / 100, 2)} {strongest.estimate >= 0 ? "the same way" : "the opposite way"}. Filled dots are significant at |t| ≥ 2.
        </Reading>
      )}
      <Chart data={data} layout={layout as any} height={Math.max(220, rows.length * 44 + 60)} />
    </>
  );
}

function RiskShares({ d }: { d: FactorOut }) {
  const rd = d.risk_decomposition;
  const rows = [...rd.table].sort((a, b) => b.share_of_total - a.share_of_total);
  const x = [...rows.map((r) => r.factor), "Idiosyncratic"];
  const y = [...rows.map((r) => r.share_of_total), 1 - rd.systematic_share];
  const donut = useMemo(
    () => (t: Tokens): Data[] => [
      {
        type: "pie",
        hole: 0.68,
        values: [rd.systematic_share, 1 - rd.systematic_share],
        labels: ["Systematic", "Idiosyncratic"],
        marker: { colors: [t.categorical[0], t.unknown], line: { color: t.surface, width: 2 } },
        textinfo: "none",
        sort: false,
        hovertemplate: "%{label}: <b>%{value:.1%}</b><extra></extra>",
      } as Data,
    ],
    [rd],
  );
  return (
    <div className="pl-riskshare">
      <div className="pl-donut">
        <Chart data={donut} layout={{ margin: { l: 0, r: 0, t: 0, b: 0 }, showlegend: false, annotations: [{ text: `<b>${fmtPct(rd.systematic_share, 0)}</b><br>systematic`, showarrow: false, font: { size: 13 } }] } as any} height={180} />
      </div>
      <Bars series={(t) => [{ name: "Share of variance", x, y, colors: x.map((_k, i) => (i === x.length - 1 ? t.unknown : (y[i] ?? 0) < 0 ? t.categorical[1] : t.categorical[0])) }]} yFormat="pct" digits={1} horizontal height={Math.max(180, x.length * 32 + 40)} />
    </div>
  );
}

function Attribution({ d }: { d: FactorOut }) {
  const f = d.attribution.cumulative_linked;
  const x = asDates(f.index);
  const parts = f.columns.filter((c) => c !== "total");
  const data = useMemo(
    () => (t: Tokens): Data[] => [
      ...parts.map(
        (c, i) =>
          ({
            type: "scatter",
            mode: "lines",
            name: c,
            x,
            y: f.data[c],
            stackgroup: "a",
            line: { width: 0.6, color: c === "residual" ? t.unknown : c === "alpha" ? t.warn : t.categorical[i % 8] },
            fillcolor: withAlpha(c === "residual" ? t.unknown : c === "alpha" ? t.warn : t.categorical[i % 8], 0.35),
            hovertemplate: `<b>${c}</b> %{y:.1%}<extra></extra>`,
          }) as Data,
      ),
      { type: "scatter", mode: "lines", name: "Total excess", x, y: f.data.total, line: { color: t.text, width: 2 }, hovertemplate: "<b>Total</b> %{y:.1%}<extra></extra>" } as Data,
    ],
    [f, x, parts],
  );
  const totals = d.attribution.totals_linked;
  const keys = [...parts, "total"].filter((k) => totals[k] !== undefined);
  return (
    <div className="grid-3">
      <div className="span-2">
        <Chart data={data} layout={{ hovermode: "x unified", margin: { l: 16, r: 8, t: 30, b: 28 }, xaxis: { type: "date" }, yaxis: { tickformat: ".0%", side: "right" }, showlegend: true } as any} height={320} />
      </div>
      <div className="stack">
        <div className="eyebrow">Totals over the sample</div>
        <KV rows={keys.map((k) => ({ label: k === "total" ? "Total excess return" : k, value: fmtSignedPct(totals[k], 1), tone: k === "total" ? "" : (totals[k] ?? 0) < 0 ? "loss" : "gain" }))} />
      </div>
    </div>
  );
}

function Rolling({ d }: { d: FactorOut }) {
  if (!d.rolling) return <div className="subtle small">History is shorter than the {d.rolling_window}-day window.</div>;
  const x = asDates(d.rolling.index);
  const cols = d.rolling.columns.filter((c) => c !== "alpha_ann" && c !== "r2").slice(0, 8);
  return <TimeSeriesChart series={cols.map((c) => ({ name: c, x, y: d.rolling!.data[c] }))} yFormat="num" digits={2} baseline={0} height={290} />;
}

function LoadingsTable({ d }: { d: FactorOut }) {
  const cols: Column<Loading>[] = [
    { key: "term", label: "Term", render: (r) => <strong>{r.term}</strong> },
    { key: "estimate_ann", label: "Estimate", numeric: true, render: (r) => (r.term === "alpha" ? <span title="annualized">{fmtSignedPct(r.estimate_ann, 2)}</span> : fmtNum(r.estimate, 3)) },
    { key: "std_error", label: "SE", numeric: true, format: (v, r) => (r.term === "alpha" ? fmtNum(v * 252 * 100, 2) + "%" : fmtNum(v, 3)), hideBelow: 900 },
    { key: "t_stat", label: "t (NW)", numeric: true, format: (v) => fmtNum(v, 2), info: INFO.nw_t, color: (v) => (Math.abs(v) >= 2 ? undefined : "subtle") },
    { key: "p_value", label: "p", numeric: true, format: (v) => (v < 0.001 ? "<0.001" : fmtNum(v, 3)) },
    { key: "ci", label: "95% CI", numeric: true, sortable: false, render: (r) => (r.term === "alpha" ? `${fmtPct(r.ci_lower * 252, 1)} … ${fmtPct(r.ci_upper * 252, 1)}` : `${fmtNum(r.ci_lower, 2)} … ${fmtNum(r.ci_upper, 2)}`), hideBelow: 600 },
  ];
  const defs = d.model.factors.filter((f): f is { name: string; long: string; short: string | null } => typeof f === "object");
  return (
    <>
      <DataTable rows={d.loadings} columns={cols} rowKey={(r) => r.term} />
      {defs.length > 0 && (
        <div className="pl-pad subtle small">
          {defs.map((f) => (
            <span key={f.name} className="pl-legdef">
              <strong>{f.name}</strong> = {f.long} − {f.short ?? "T-bill"}
            </span>
          ))}
          <InfoTip info={{ text: "Factor legs actually used. Preset factors whose ETFs have no data here are dropped (see notes)." }} size={12} />
        </div>
      )}
    </>
  );
}
