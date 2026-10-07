/**
 * Factors — what the portfolio is exposed to.
 *   GET  /api/factors/models      model catalogue + ETF preset
 *   POST /api/factors/regression  Fama–French / Carhart on Ken French's daily factors
 *   POST /api/factors/custom      the same on tradable long/short ETF spreads (works offline)
 * When the French library is unreachable (503) the cell says DATA UNAVAILABLE and offers ETF.
 */
import { useMemo, useState } from "react";
import { BarChart, DataTable, InfoTip, Panel, Section, SegmentedControl, Select, StatGrid, StatTile, TimeSeriesChart, useTabParam, type Column } from "../../components";
import { CoefChart } from "../../charts/CoefChart";
import { Absent } from "../../design";
import { DataUnavailableError } from "../../lib/api";
import { fmtDate, fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import { useApiPost, useApiQuery } from "../../lib/query";
import type { PortfolioIn } from "../../lib/types";
import { INFO } from "./info";
import { KV, RunButton, asDates, useCommitted } from "./shared";
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
    <Section
      title={source === "french" ? `Factors · ${spec?.label ?? "Fama–French"}` : "Factors · ETF spreads"}
      actions={
        <div className="pl-controls">
          <SegmentedControl
            size="sm"
            ariaLabel="Factor source"
            options={[
              { value: "french", label: "FF", title: "Kenneth French data library" },
              { value: "etf", label: "ETF", title: "Tradable long/short ETF spreads" },
            ]}
            value={source}
            onChange={setSource}
          />
          {source === "french" && models.data && <Select ariaLabel="Factor model" value={model} onChange={setModel} options={models.data.models.map((m) => ({ value: m.key, label: m.label }))} />}
          <SegmentedControl size="sm" ariaLabel="Rebalancing" options={[{ value: "daily", label: "D" }, { value: "monthly", label: "M" }, { value: "none", label: "B&H" }]} value={rebalance} onChange={setRebalance} />
        </div>
      }
    >
      {source === "french" && spec && (
        <div className="pl-spec num">
          <span>{spec.factors.join(" · ")}</span>
          <span className="subtle">{spec.reference}</span>
        </div>
      )}

      {source === "french" && frenchDown && (
        <div className="grid-3">
          <Panel title="FF factors" error={french.error} onRetry={() => french.refetch()} span={2} />
          <Panel title="ETF factors">
            <button type="button" className="btn btn-sm" onClick={() => setSource("etf")}>
              USE ETF FACTORS
            </button>
          </Panel>
        </div>
      )}

      {source === "etf" && <FactorEditor defs={defs} onChange={(d) => (setServerPreset(false), setDefs(d))} onRun={run} dirty={dirty} busy={custom.isFetching} preset={models.data?.etf_preset} serverPreset={serverPreset} onServerPreset={() => setServerPreset(true)} />}

      {!(source === "french" && frenchDown) && <FactorResults q={q} />}
    </Section>
  );
}

// ------------------------------------------------------------------ custom factor editor

function FactorEditor({ defs, onChange, onRun, dirty, busy, preset, serverPreset, onServerPreset }: { defs: FactorDef[]; onChange: (d: FactorDef[]) => void; onRun: () => void; dirty: boolean; busy: boolean; preset?: FactorModelsOut["etf_preset"]; serverPreset: boolean; onServerPreset: () => void }) {
  if (serverPreset && preset)
    return (
      <Panel title={`ETF factors · server preset · ${preset.length}`} actions={<RunButton onRun={onRun} dirty={dirty} busy={busy} />}>
        <div className="pl-preset-list num">
          {preset.map((p) => (
            <div key={p.name} className="pl-preset">
              <span>{p.name}</span>
              <span>
                {p.long} − {p.short ?? "T-BILL"}
              </span>
              <span className="subtle">{p.label}</span>
            </div>
          ))}
        </div>
        <div className="pl-btnrow">
          <button type="button" className="btn btn-sm" onClick={() => onChange(DEFAULT_ETF_FACTORS)}>
            EDIT LEGS
          </button>
        </div>
      </Panel>
    );
  const set = (i: number, patch: Partial<FactorDef>) => onChange(defs.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  return (
    <Panel title="ETF factors · legs" actions={<RunButton onRun={onRun} dirty={dirty} busy={busy} />}>
      <div className="pl-factor-defs">
        <div className="pl-fd-row pl-fd-head">
          <span>NAME</span>
          <span>LONG</span>
          <span />
          <span>SHORT</span>
          <span />
        </div>
        {defs.map((d, i) => (
          <div className="pl-fd-row" key={i}>
            <input className="input num" aria-label="Factor name" value={d.name} maxLength={24} onChange={(e) => set(i, { name: e.target.value.toUpperCase() })} />
            <input className="input num" aria-label="Long leg" value={d.long} maxLength={12} onChange={(e) => set(i, { long: e.target.value.toUpperCase() })} />
            <span className="pl-fd-minus" aria-hidden>
              −
            </span>
            <input className="input num" aria-label="Short leg" placeholder="T-BILL" value={d.short ?? ""} maxLength={12} onChange={(e) => set(i, { short: e.target.value.toUpperCase() || null })} />
            <button type="button" className="btn btn-sm" aria-label={`DEL ${d.name}`} onClick={() => onChange(defs.filter((_, j) => j !== i))} disabled={defs.length <= 1}>
              DEL
            </button>
          </div>
        ))}
      </div>
      <div className="pl-btnrow">
        <button type="button" className="btn btn-sm" onClick={() => onChange([...defs, { name: `F${defs.length + 1}`, long: "", short: "SPY" }])} disabled={defs.length >= 10}>
          ADD
        </button>
        <button type="button" className="btn btn-sm" onClick={() => onChange(DEFAULT_ETF_FACTORS)}>
          RESET
        </button>
        {preset && (
          <button type="button" className="btn btn-sm" onClick={onServerPreset}>
            SERVER PRESET · {preset.length}
          </button>
        )}
      </div>
    </Panel>
  );
}

// ------------------------------------------------------------------ results

type Q = ReturnType<typeof useApiPost<FactorOut>>;

function FactorResults({ q }: { q: Q }) {
  const asOf = q.data?.stats.end ?? undefined;
  return (
    <>
      <Panel<FactorOut> query={q} skeletonHeight={110} compact notes={[]} asOf={asOf}>
        {(d) => <FactorHeadline d={d} />}
      </Panel>
      <div className="grid-2">
        <Panel<FactorOut> title="Loadings · 95% CI · NW" info={INFO.nw_t} query={q} skeletonHeight={300} notes={[]} asOf={asOf}>
          {(d) => <CoefChart rows={factorsOf(d).map((r) => ({ term: r.term, est: r.estimate, lo: r.ci_lower, hi: r.ci_upper, t: r.t_stat }))} ariaLabel="Factor loadings with 95% confidence intervals" />}
        </Panel>
        <Panel<FactorOut> title="Variance share" info={INFO.factor_risk} query={q} skeletonHeight={300} notes={[]} asOf={asOf}>
          {(d) => <RiskShares d={d} />}
        </Panel>
      </div>
      <Panel<FactorOut> title="Attribution · cumulative" info={INFO.carino} query={q} skeletonHeight={340} notes={[]} asOf={asOf}>
        {(d) => <Attribution d={d} />}
      </Panel>
      <div className="grid-2">
        <Panel<FactorOut> title={q.data ? `Rolling loadings · ${q.data.rolling_window}D` : "Rolling loadings"} query={q} skeletonHeight={300} notes={[]} asOf={asOf}>
          {(d) => <Rolling d={d} />}
        </Panel>
        <Panel<FactorOut> title="Loadings · detail" query={q} flush skeletonHeight={300} asOf={asOf}>
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
      <StatTile label="R²" value={fmtNum(s.r2, 2)} info={INFO.r2} caption={<span className="num">ADJ {fmtNum(s.adj_r2, 3)}</span>} />
      <StatTile label="Alpha ann" value={s.alpha_ann} format={(v) => fmtSignedPct(v, 2)} tone="auto" info={INFO.factor_alpha} caption={<span className="num">t {fmtNum(s.alpha_t, 2)}{Math.abs(s.alpha_t) < 2 ? " · NS" : ""}</span>} />
      <StatTile label="Systematic" value={fmtPct(rd.systematic_share, 0)} info={INFO.factor_risk} caption={<span className="num">{fmtPct(rd.systematic_vol_ann, 1)} OF {fmtPct(rd.total_vol_ann, 1)}</span>} />
      <StatTile label="Idio vol" value={fmtPct(s.residual_vol_ann, 1)} info={{ text: "Annualized volatility of the regression residual." }} />
      <StatTile label="Appraisal" value={s.appraisal_ratio} format={(v) => fmtNum(v, 2)} tone="auto" info={INFO.appraisal} />
      <StatTile label="N" value={fmtNum(s.n_obs, 0)} caption={<span className="num">{fmtDate(s.start, "month")} – {fmtDate(s.end, "month")} · NW {s.nw_lags}</span>} info={{ text: "Daily observations and Newey–West lag length." }} />
    </StatGrid>
  );
}

const factorsOf = (d: FactorOut) => d.loadings.filter((l) => l.term !== "alpha");

function RiskShares({ d }: { d: FactorOut }) {
  const rd = d.risk_decomposition;
  const rows = [...rd.table].sort((a, b) => b.share_of_total - a.share_of_total);
  const x = [...rows.map((r) => r.factor), "IDIO"];
  const y = [...rows.map((r) => r.share_of_total), 1 - rd.systematic_share];
  return <BarChart horizontal x={x} y={y} yFormat="pct" digits={1} height={Math.max(160, x.length * 26 + 12)} />;
}

function Attribution({ d }: { d: FactorOut }) {
  const f = d.attribution.cumulative_linked;
  const parts = useMemo(() => f.columns.filter((c) => c !== "total"), [f]);
  const series = useMemo(() => {
    const x = asDates(f.index);
    const tones = ["var(--ink-2)", "var(--c5)", "var(--c6)", "var(--c7)", "var(--c8)", "var(--ink-3)"];
    return [
      { name: "TOTAL", x, y: f.data.total, color: "var(--ink)", width: 1.5 },
      ...parts.map((c, i) => ({ name: c.toUpperCase(), x, y: f.data[c], color: c === "residual" ? "var(--ink-3)" : tones[i % tones.length], dash: c === "residual" || c === "alpha" ? ("dot" as const) : undefined })),
    ];
  }, [f, parts]);
  const totals = d.attribution.totals_linked;
  const keys = ["total", ...parts].filter((k) => totals[k] !== undefined);
  return (
    <div className="grid-3">
      <div className="span-2">
        <TimeSeriesChart series={series} yFormat="pct" digits={1} height={320} baseline={0} />
      </div>
      <KV rows={keys.map((k) => ({ label: k === "total" ? "Total excess" : k.toUpperCase(), value: fmtSignedPct(totals[k], 1), tone: (totals[k] ?? 0) < 0 ? "loss" : "" }))} />
    </div>
  );
}

function Rolling({ d }: { d: FactorOut }) {
  if (!d.rolling) return <Absent reason={`HISTORY SHORTER THAN ${d.rolling_window}D`} />;
  const x = asDates(d.rolling.index);
  const cols = d.rolling.columns.filter((c) => c !== "alpha_ann" && c !== "r2").slice(0, 6);
  const tones = ["var(--ink)", "var(--ink-2)", "var(--c5)", "var(--c6)", "var(--c7)", "var(--c8)"];
  return <TimeSeriesChart series={cols.map((c, i) => ({ name: c, x, y: d.rolling!.data[c], color: tones[i] }))} yFormat="num" digits={2} baseline={0} height={290} />;
}

function LoadingsTable({ d }: { d: FactorOut }) {
  const cols: Column<Loading>[] = [
    { key: "term", label: "Term", render: (r) => <span className="num">{r.term.toUpperCase()}</span> },
    { key: "estimate_ann", label: "Est", numeric: true, render: (r) => (r.term === "alpha" ? <span title="annualized">{fmtSignedPct(r.estimate_ann, 2)}</span> : fmtNum(r.estimate, 3)) },
    { key: "std_error", label: "SE", numeric: true, format: (v, r) => (r.term === "alpha" ? fmtPct(v * 252, 2) : fmtNum(v, 3)), hideBelow: 900 },
    { key: "t_stat", label: "t NW", numeric: true, format: (v) => fmtNum(v, 2), info: INFO.nw_t, color: (v) => (Math.abs(v) >= 2 ? undefined : "subtle") },
    { key: "p_value", label: "p", numeric: true, format: (v) => (v < 0.001 ? "<0.001" : fmtNum(v, 3)) },
    { key: "ci", label: "95% CI", numeric: true, sortable: false, render: (r) => (r.term === "alpha" ? `${fmtPct(r.ci_lower * 252, 1)} … ${fmtPct(r.ci_upper * 252, 1)}` : `${fmtNum(r.ci_lower, 2)} … ${fmtNum(r.ci_upper, 2)}`), hideBelow: 600 },
  ];
  const defs = d.model.factors.filter((f): f is { name: string; long: string; short: string | null } => typeof f === "object");
  return (
    <>
      <DataTable rows={d.loadings} columns={cols} rowKey={(r) => r.term} compact />
      {defs.length > 0 && (
        <div className="pl-foot num">
          {defs.map((f) => (
            <span key={f.name} className="pl-legdef">
              {f.name} = {f.long} − {f.short ?? "T-BILL"}
            </span>
          ))}
          <InfoTip info={{ text: "Legs used. Preset factors without data here are dropped (see notes)." }} size={12} label="Legs" />
        </div>
      )}
    </>
  );
}
