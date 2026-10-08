/**
 * Overview — POST /api/performance/analyze: the tear sheet.
 * Headline ratios with sampling error, growth vs benchmark, drawdowns, monthly calendar,
 * rolling statistics, return distribution and the PSR / MinTRL "is this luck?" read-out.
 */
import { useMemo, useState, type CSSProperties } from "react";
import { DataTable, NumberField, Panel, Section, SegmentedControl, StatGrid, StatTile, TimeSeriesChart, Toggle, type Column, type LineSeries } from "../../components";
import { XYChart } from "../../charts/XYChart";
import { Absent, Verdict } from "../../design";
import { fmtCurrency, fmtDate, fmtMultiple, fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import type { PortfolioIn } from "../../lib/types";
import { INFO } from "./info";
import { KV, Readline, asDates } from "./shared";
import type { DrawdownEpisode, PerformanceOut, Rebalance } from "./types";

const REBALANCE: { value: Rebalance; label: string; title: string }[] = [
  { value: "daily", label: "D", title: "Rebalance daily (constant mix)" },
  { value: "monthly", label: "M", title: "Rebalance month end" },
  { value: "quarterly", label: "Q", title: "Rebalance quarter end" },
  { value: "none", label: "B&H", title: "Buy and hold" },
];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]; // monthly_table keys

export function OverviewTab({ req }: { req: PortfolioIn }) {
  const [rebalance, setRebalance] = useState<Rebalance>("monthly");
  const [hurdle, setHurdle] = useState(0);
  const [conf, setConf] = useState<"0.9" | "0.95" | "0.99">("0.95");
  const [logY, setLogY] = useState(false);
  const body = useMemo(() => ({ ...req, rebalance, sr_benchmark: hurdle, psr_confidence: Number(conf), top_drawdowns: 5 }), [req, rebalance, hurdle, conf]);
  const q = useApiPost<PerformanceOut>("/performance/analyze", body);
  const bench = req.benchmark || "SPY";
  const notional = req.notional ?? 1_000_000;
  const asOf = q.data?.portfolio.end ?? undefined;

  return (
    <>
      <Section title={`Performance · vs ${bench}`} actions={<SegmentedControl ariaLabel="Rebalancing" size="sm" options={REBALANCE} value={rebalance} onChange={setRebalance} />}>
        <Panel<PerformanceOut> query={q} skeletonHeight={120} compact asOf={asOf}>
          {(d) => <Headline d={d} bench={bench} />}
        </Panel>

        <div className="grid-3">
          <Panel<PerformanceOut> span={2} title={`Growth · ${fmtCurrency(notional, { compact: true })}`} query={q} skeletonHeight={360} asOf={asOf} actions={<Toggle label="LOG" checked={logY} onChange={setLogY} />} notes={[]}>
            {(d) => <EquityChart d={d} notional={notional} logY={logY} />}
          </Panel>
          <Panel<PerformanceOut> title={`Relative · ${bench}`} query={q} skeletonHeight={360} asOf={asOf} notes={[]}>
            {(d) => <RelativeCard d={d} bench={bench} />}
          </Panel>
        </div>
      </Section>

      <Section title="Drawdowns">
        <Panel<PerformanceOut> title="Under water" info="max_drawdown" query={q} skeletonHeight={280} asOf={asOf} notes={[]}>
          {(d) => <DrawdownChart d={d} bench={bench} />}
        </Panel>
        <Panel<PerformanceOut> title="Worst 5" query={q} flush skeletonHeight={220} asOf={asOf} notes={[]}>
          {(d) => <EpisodesTable rows={d.drawdowns} />}
        </Panel>
      </Section>

      <Section title="Monthly">
        <Panel<PerformanceOut> query={q} flush skeletonHeight={320} asOf={asOf} notes={[]} title="Returns · calendar month">
          {(d) => <MonthlyTable d={d} />}
        </Panel>
      </Section>

      <Section title="Stability">
        <Panel<PerformanceOut> title={`Rolling ${q.data ? Math.round(q.data.rolling_window / 21) : 12}M`} query={q} skeletonHeight={440} asOf={asOf} notes={[]}>
          {(d) => <RollingGrid d={d} bench={bench} />}
        </Panel>
        <div className="grid-2">
          <Panel<PerformanceOut> title="Daily returns" query={q} skeletonHeight={300} asOf={asOf} notes={[]}>
            {(d) => <Distribution d={d} />}
          </Panel>
          <Panel<PerformanceOut>
            title="PSR · MinTRL"
            info={INFO.psr}
            query={q}
            skeletonHeight={300}
            asOf={asOf}
            notes={[]}
            actions={
              <>
                <NumberField value={hurdle} onChange={setHurdle} min={-1} max={3} step={0.1} width={78} digits={2} ariaLabel="Hurdle Sharpe ratio" />
                <SegmentedControl size="sm" ariaLabel="Confidence" options={[{ value: "0.9", label: "90" }, { value: "0.95", label: "95" }, { value: "0.99", label: "99" }]} value={conf} onChange={setConf} />
              </>
            }
          >
            {(d) => <SharpeInference d={d} />}
          </Panel>
        </div>
      </Section>
    </>
  );
}

// ------------------------------------------------------------------ headline

function Headline({ d, bench }: { d: PerformanceOut; bench: string }) {
  const s = d.summary;
  const b = d.benchmark_summary;
  const r = d.relative;
  const si = d.sharpe_inference;
  const rfZero = (s.rf_ann ?? 0) === 0;
  return (
    <StatGrid min={128}>
      <StatTile label="CAGR" value={s.cagr} format={(v) => fmtSignedPct(v, 1)} tone="auto" info="cagr" delta={b && s.cagr != null && b.cagr != null ? s.cagr - b.cagr : null} deltaFormat={(x) => fmtPct(x, 1, { signed: true })} deltaLabel={`vs ${bench}`} />
      <StatTile label="Vol" value={s.vol_ann} format={(v) => fmtPct(v, 1)} info="vol" delta={b && s.vol_ann != null && b.vol_ann != null ? s.vol_ann - b.vol_ann : null} invert deltaFormat={(x) => fmtPct(x, 1, { signed: true })} deltaLabel={`vs ${bench}`} />
      <StatTile label="Sharpe" value={s.sharpe} format={(v) => fmtNum(v, 2)} info={INFO.sharpe_se} caption={si.se_hac != null ? <span className="num">±{fmtNum(si.se_hac, 2)} SE{rfZero ? " · RF 0" : ""}</span> : undefined} />
      <StatTile label="Sortino" value={s.sortino} format={(v) => fmtNum(v, 2)} info="sortino" caption={b ? <span className="num">{bench} {fmtNum(b.sortino, 2)}</span> : undefined} />
      <StatTile label="Max DD" value={s.max_drawdown} format={(v) => fmtPct(v, 1)} tone="loss" info="max_drawdown" caption={s.max_drawdown_trough ? <span className="num">TROUGH {fmtDate(s.max_drawdown_trough, "month")}</span> : undefined} />
      <StatTile label={`Beta · ${bench}`} value={r?.beta ?? null} format={(v) => fmtNum(v, 2)} info="beta" caption={r?.r2 != null ? <span className="num">R² {fmtNum(r.r2, 2)}</span> : undefined} />
      <StatTile label="Alpha ann" value={r?.alpha_ann ?? null} format={(v) => fmtSignedPct(v, 2)} tone="auto" info={INFO.alpha} caption={r?.alpha_t != null ? <span className="num">t {fmtNum(r.alpha_t, 2)}{Math.abs(r.alpha_t) < 2 ? " · NS" : ""}</span> : undefined} />
      <StatTile label="IR" value={r?.information_ratio ?? null} format={(v) => fmtNum(v, 2)} tone="auto" info={INFO.information_ratio} caption={r?.tracking_error != null ? <span className="num">TE {fmtPct(r.tracking_error, 1)}</span> : undefined} />
    </StatGrid>
  );
}

// ------------------------------------------------------------------ equity

function EquityChart({ d, notional, logY }: { d: PerformanceOut; notional: number; logY: boolean }) {
  const series = useMemo(() => {
    const x = asDates(d.equity.index);
    const out = [{ name: "PORT", x, y: (d.equity.data.portfolio ?? []).map((v) => (v == null ? null : v * notional)), color: "var(--ink)", width: 1.5 }];
    if (d.equity.data.benchmark) out.push({ name: d.portfolio.benchmark ?? "BENCH", x, y: d.equity.data.benchmark.map((v) => (v == null ? null : v * notional)), color: "var(--ink-2)", width: 1.25 });
    return out;
  }, [d, notional]);
  const end = series[0].y[series[0].y.length - 1];
  const bEnd = series[1]?.y[series[1].y.length - 1];
  return (
    <>
      <Readline
        items={[
          { k: "START", v: fmtDate(d.portfolio.start) },
          { k: "END", v: fmtCurrency(end, { compact: true, digits: 2 }) },
          ...(bEnd != null ? [{ k: d.portfolio.benchmark ?? "BENCH", v: fmtCurrency(bEnd, { compact: true, digits: 2 }) }] : []),
        ]}
      />
      <TimeSeriesChart series={series} yFormat="usd" digits={0} logY={logY} height={330} baseline={notional} />
    </>
  );
}

function RelativeCard({ d, bench }: { d: PerformanceOut; bench: string }) {
  const r = d.relative;
  const s = d.summary;
  const to = d.portfolio.turnover;
  if (!r) return <Absent reason="NO BENCHMARK" />;
  return (
    <KV
      rows={[
        { label: "Active return", value: fmtSignedPct(r.active_return_ann, 2), tone: r.active_return_ann != null && r.active_return_ann < 0 ? "loss" : "", info: { text: `Annualized mean daily return difference vs ${bench}.` } },
        { label: "Tracking error", value: fmtPct(r.tracking_error, 2), info: INFO.tracking_error },
        { label: "Up capture", value: fmtPct(r.up_capture, 0), info: INFO.capture },
        { label: "Down capture", value: fmtPct(r.down_capture, 0), info: INFO.capture },
        { label: "Correlation", value: fmtNum(r.correlation, 2), info: INFO.correlation },
        { label: "Treynor", value: fmtPct(r.treynor, 2), info: INFO.treynor },
        { label: "M²", value: fmtPct(r.m2, 2), info: INFO.m2, hint: r.m2_excess != null ? `${fmtSignedPct(r.m2_excess, 2)} vs ${bench}` : undefined },
        { label: "Calmar", value: fmtNum(s.calmar, 2), info: INFO.calmar },
        { label: "Omega", value: fmtNum(s.omega, 2), info: INFO.omega },
        { label: "Ulcer", value: fmtPct(s.ulcer_index, 1), info: INFO.ulcer },
        ...(to ? [{ label: "Turnover / yr", value: fmtPct(to.annual, 1), info: INFO.turnover, hint: `${to.rebalances} REBAL` }] : []),
      ]}
    />
  );
}

// ------------------------------------------------------------------ drawdowns

function DrawdownChart({ d, bench }: { d: PerformanceOut; bench: string }) {
  const series = useMemo(() => {
    const x = asDates(d.drawdown.index);
    const out: LineSeries[] = [{ name: "PORT", x, y: d.drawdown.data.portfolio, color: "var(--signal)", width: 1.25, fill: true }];
    if (d.drawdown.data.benchmark) out.push({ name: bench, x, y: d.drawdown.data.benchmark, color: "var(--ink-2)", dash: "dot" });
    return out;
  }, [d, bench]);
  return <TimeSeriesChart series={series} yFormat="pct" digits={1} height={280} baseline={0} />;
}

function EpisodesTable({ rows }: { rows: DrawdownEpisode[] }) {
  const cols: Column<DrawdownEpisode & { i: number }>[] = [
    { key: "i", label: "#", numeric: true, width: 28 },
    { key: "depth", label: "Depth", numeric: true, format: (v) => fmtPct(v, 1), className: "loss" },
    { key: "peak", label: "Peak", format: (v) => fmtDate(v, "iso") },
    { key: "trough", label: "Trough", format: (v) => fmtDate(v, "iso") },
    { key: "recovery", label: "Recovered", format: (v) => (v ? fmtDate(v, "iso") : "—"), hideBelow: 600 },
    { key: "decline", label: "Decline", numeric: true, format: (v) => `${fmtNum(v, 0)}D`, hideBelow: 900 },
    { key: "recovery_periods", label: "Recovery", numeric: true, render: (r) => (r.recovered ? <span className="num">{fmtNum(r.recovery_periods, 0)}D</span> : <span className="num loss">OPEN</span>) },
    { key: "length", label: "Length", numeric: true, format: (v) => `${fmtNum(v, 0)}D`, hideBelow: 900 },
  ];
  return <DataTable rows={rows.map((r, i) => ({ ...r, i: i + 1 }))} columns={cols} rowKey={(r) => r.peak} compact />;
}

// ------------------------------------------------------------------ monthly

type MonthRow = Record<string, number | null> & { year: number };

function MonthlyTable({ d }: { d: PerformanceOut }) {
  const rows = useMemo(() => [...(d.monthly_table.rows as MonthRow[])].sort((a, b) => b.year - a.year), [d]);
  const vals = rows.flatMap((r) => MONTHS.map((m) => r[m])).filter((v): v is number => typeof v === "number");
  const scale = Math.max(0.03, ...vals.map((v) => Math.abs(v))) * 0.9;
  const cols: Column<MonthRow>[] = [
    { key: "year", label: "Year", render: (r) => <span className="num">{r.year}</span>, width: 56 },
    ...MONTHS.map<Column<MonthRow>>((m) => ({ key: m, label: m, numeric: true, format: (v) => (v == null ? "" : fmtPct(v, 1)), heat: { min: -scale, max: scale }, sortable: false })),
    { key: "Annual", label: "Year", numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), color: "sign" },
  ];
  return <DataTable rows={rows} columns={cols} rowKey={(r) => r.year} className="pl-monthly" compact />;
}

// ------------------------------------------------------------------ rolling

function RollingGrid({ d, bench }: { d: PerformanceOut; bench: string }) {
  if (!d.rolling) return <Absent reason={`HISTORY SHORTER THAN ${d.rolling_window}D`} />;
  const x = asDates(d.rolling.index);
  const col = (k: string) => d.rolling!.data[k] ?? [];
  const charts: { title: string; key: string; fmt: "pct" | "num"; base?: number }[] = [
    { title: "SHARPE", key: "sharpe", fmt: "num", base: 0 },
    { title: "VOL", key: "vol", fmt: "pct" },
    { title: `BETA · ${bench}`, key: "beta", fmt: "num", base: 1 },
    { title: "RETURN", key: "return", fmt: "pct", base: 0 },
  ];
  return (
    <div className="pl-small-multiples">
      {charts.map((c) => (
        <div key={c.key} className="pl-sm">
          <div className="pl-sm-head">{c.title}</div>
          <TimeSeriesChart series={[{ name: c.title, x, y: col(c.key), color: "var(--ink)" }]} yFormat={c.fmt} digits={2} height={190} baseline={c.base} showLegend={false} />
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ distribution

function Distribution({ d }: { d: PerformanceOut }) {
  const s = d.summary;
  const h = d.returns_histogram;
  const centers = useMemo(() => h.edges.slice(0, -1).map((e, i) => (e + h.edges[i + 1]) / 2), [h]);
  const series = useMemo(() => [{ name: "DAYS", y: h.counts, mode: "bars" as const, tone: "ink3" as const }], [h]);
  const vlines = [
    ...(s.var != null ? [{ at: -s.var, label: `VAR ${fmtNum((s.var_level ?? 0.95) * 100, 0)}` }] : []),
    ...(s.cvar != null ? [{ at: -s.cvar, label: "CVAR", dash: "dot" as const }] : []),
  ];
  return (
    <>
      <XYChart x={centers} series={series} vlines={vlines} xFormat="pct" yFormat="int" digits={0} height={220} zero ariaLabel="Histogram of daily portfolio returns with historical VaR and CVaR" />
      <KV
        cols={2}
        rows={[
          { label: "Skew", value: fmtNum(s.skew, 2), info: INFO.skew },
          { label: "Excess kurt", value: fmtNum(s.excess_kurtosis, 1), info: INFO.kurtosis },
          { label: "Hit rate", value: fmtPct(s.hit_rate, 1), info: { text: "Share of days with a positive return." } },
          { label: "Payoff", value: fmtMultiple(s.payoff_ratio, 2), info: { text: "Average up day / average down day (absolute)." } },
          { label: "Best day", value: fmtSignedPct(s.best_day, 2), hint: fmtDate(s.best_day_date, "iso") },
          { label: "Worst day", value: fmtSignedPct(s.worst_day, 2), tone: "loss", hint: fmtDate(s.worst_day_date, "iso") },
        ]}
      />
    </>
  );
}

// ------------------------------------------------------------------ PSR / MinTRL

function SharpeInference({ d }: { d: PerformanceOut }) {
  const si = d.sharpe_inference;
  const years = d.summary.years;
  const need = si.min_track_record_years;
  const finite = need != null && Number.isFinite(need);
  const conf = si.min_track_record_confidence;
  const max = Math.max(years, finite ? need : 0) * 1.1 || 1;
  const hurdle = si.psr_benchmark_sharpe;
  const psr = si.psr;
  const pass = psr != null && psr >= conf;
  return (
    <div className="stack">
      <Verdict value={psr == null ? "INSUFFICIENT DATA" : pass ? "PASS" : "FAIL"} detail={psr == null ? "PSR NOT COMPUTABLE" : `PSR ${fmtPct(psr, 1)} ${pass ? "≥" : "<"} ${fmtPct(conf, 0)} · SR* ${fmtNum(hurdle, 2)}`} />
      <div className="pl-trl" aria-label="Track record against minimum track record length">
        <div className="pl-trl-head num">
          <span>RECORD {fmtNum(years, 1)}Y</span>
          <span>MINTRL {fmtPct(conf, 0)} {finite ? `${fmtNum(need, 1)}Y` : "NEVER · SR ≤ SR*"}</span>
        </div>
        <div className="pl-trl-track">
          <div className={`pl-trl-fill ${si.track_record_sufficient ? "ok" : "short"}`} style={{ "--w": `${Math.min(100, (years / max) * 100)}%` } as CSSProperties} />
          {finite && <div className="pl-trl-mark" style={{ "--x": `${Math.min(100, (need / max) * 100)}%` } as CSSProperties} title={`MinTRL ${fmtNum(need, 2)} years`} />}
        </div>
      </div>
      <KV
        cols={2}
        rows={[
          { label: "SE IID", value: fmtNum(si.se_iid, 3), info: INFO.sharpe_se },
          { label: "SE fat tails", value: fmtNum(si.se_nonnormal, 3), info: { text: "Mertens (2002) skew/kurtosis correction." } },
          { label: "SE HAC", value: fmtNum(si.se_hac, 3), info: { text: "Newey–West, robust to autocorrelation (Lo 2002)." } },
          { label: "95% CI", value: `${fmtNum(si.ci95_lower, 2)} – ${fmtNum(si.ci95_upper, 2)}`, info: { text: "Sharpe ± 1.96 HAC SE." } },
          { label: "t HAC", value: fmtNum(si.t_stat_hac, 2), info: INFO.sharpe_se },
          { label: "MinTRL", value: finite ? `${fmtNum(need, 1)}Y` : "—", info: INFO.mintrl },
        ]}
      />
    </div>
  );
}
