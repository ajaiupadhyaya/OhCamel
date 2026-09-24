/**
 * Overview — POST /api/performance/analyze: the tear sheet.
 * Headline ratios with sampling error, growth vs benchmark, drawdowns, monthly calendar,
 * rolling statistics, return distribution and the PSR / MinTRL "is this luck?" read-out.
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, NumberField, Panel, Section, SegmentedControl, StatGrid, StatTile, TimeSeriesChart, Toggle, withAlpha, type Column } from "../../components";
import { fmtCurrency, fmtDate, fmtMultiple, fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import type { PortfolioIn } from "../../lib/types";
import { INFO } from "./info";
import { KV, Reading, asDates } from "./shared";
import type { DrawdownEpisode, PerformanceOut, Rebalance } from "./types";

const REBALANCE: { value: Rebalance; label: string; title: string }[] = [
  { value: "daily", label: "Daily", title: "Reset to target weights every session (constant mix)" },
  { value: "monthly", label: "Monthly", title: "Let weights drift, reset at each month end" },
  { value: "quarterly", label: "Quarterly", title: "Let weights drift, reset at each quarter end" },
  { value: "none", label: "Buy & hold", title: "Never rebalance — weights drift with prices" },
];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function OverviewTab({ req }: { req: PortfolioIn }) {
  const [rebalance, setRebalance] = useState<Rebalance>("monthly");
  const [hurdle, setHurdle] = useState(0);
  const [conf, setConf] = useState<"0.9" | "0.95" | "0.99">("0.95");
  const [logY, setLogY] = useState(false);
  const body = useMemo(() => ({ ...req, rebalance, sr_benchmark: hurdle, psr_confidence: Number(conf), top_drawdowns: 5 }), [req, rebalance, hurdle, conf]);
  const q = useApiPost<PerformanceOut>("/performance/analyze", body);
  const bench = req.benchmark || "SPY";
  const notional = req.notional ?? 1_000_000;

  return (
    <>
      <Section
        title="Performance"
        description={`How the portfolio actually grew, and at what risk, compared with ${bench}. All statistics use daily adjusted closes on the sessions every holding traded (the notes say whether the source includes dividends).`}
        actions={<SegmentedControl ariaLabel="Rebalancing" size="sm" options={REBALANCE} value={rebalance} onChange={setRebalance} />}
      >
        <Panel<PerformanceOut> query={q} skeletonHeight={120} compact>
          {(d) => <Headline d={d} bench={bench} />}
        </Panel>

        <div className="grid-3">
          <Panel<PerformanceOut>
            span={2}
            title={`Growth of ${fmtCurrency(notional, { compact: true })}`}
            subtitle={`What the notional would be worth today, next to the same amount in ${bench}. Log scale makes equal percentage moves look equal.`}
            info={{ text: "Cumulative value of the portfolio (after rebalancing drift) and of the benchmark, starting from the notional on the first common session." }}
            query={q}
            skeletonHeight={360}
            actions={<Toggle label="Log" checked={logY} onChange={setLogY} />}
            notes={[]}
          >
            {(d) => <EquityChart d={d} notional={notional} logY={logY} />}
          </Panel>
          <Panel<PerformanceOut> title="Relative to benchmark" subtitle={`Where the return came from versus ${bench}.`} query={q} skeletonHeight={360} notes={[]}>
            {(d) => <RelativeCard d={d} bench={bench} />}
          </Panel>
        </div>
      </Section>

      <Section title="Drawdowns" description="The losses you would have had to sit through, how deep they went and how long recovery took.">
        <Panel<PerformanceOut> title="Under water" subtitle="Percent below the previous peak. Shaded bands mark the five worst episodes, numbered by depth." info="max_drawdown" query={q} skeletonHeight={280} notes={[]}>
          {(d) => <DrawdownChart d={d} bench={bench} />}
        </Panel>
        <Panel<PerformanceOut> title="Worst episodes" subtitle="Peak to trough, then back to the old high. Recovery and length are in trading days." query={q} flush skeletonHeight={220} notes={[]}>
          {(d) => <EpisodesTable rows={d.drawdowns} />}
        </Panel>
      </Section>

      <Section title="Month by month" description="Calendar-month returns of the portfolio. Read across for a year, down a column for seasonality.">
        <Panel<PerformanceOut> query={q} flush skeletonHeight={320} notes={[]} info={{ text: "Compounded daily returns within each calendar month; 'Year' compounds the months shown." }} title="Monthly returns">
          {(d) => <MonthlyTable d={d} />}
        </Panel>
      </Section>

      <Section title="Stability" description="Is the performance steady, or did it come from one lucky stretch? Rolling one-year statistics and the shape of daily returns.">
        <Panel<PerformanceOut> title={`Rolling ${q.data ? Math.round(q.data.rolling_window / 21) : 12}-month statistics`} subtitle="Each point summarizes the trailing window ending that day." query={q} skeletonHeight={440} notes={[]} info={{ text: "Sharpe, volatility, beta and return recomputed on a moving window. Wide swings mean the full-period numbers hide very different regimes." }}>
          {(d) => <RollingGrid d={d} bench={bench} />}
        </Panel>
        <div className="grid-2">
          <Panel<PerformanceOut> title="Daily return distribution" subtitle="How often each size of daily move happened. The markers show historical VaR and CVaR (ES)." query={q} skeletonHeight={300} notes={[]} info="var">
            {(d) => <Distribution d={d} />}
          </Panel>
          <Panel<PerformanceOut>
            title="Is the Sharpe ratio real?"
            subtitle="A Sharpe ratio is an estimate. These tests ask whether it could be luck, given how long and how fat-tailed the record is."
            info={INFO.psr}
            query={q}
            skeletonHeight={300}
            notes={[]}
            actions={
              <>
                <span className="subtle small">Hurdle SR*</span>
                <NumberField value={hurdle} onChange={setHurdle} min={-1} max={3} step={0.1} width={78} digits={2} />
                <SegmentedControl size="sm" ariaLabel="Confidence" options={[{ value: "0.9", label: "90%" }, { value: "0.95", label: "95%" }, { value: "0.99", label: "99%" }]} value={conf} onChange={setConf} />
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
      <StatTile label="Volatility" value={s.vol_ann} format={(v) => fmtPct(v, 1)} info="vol" delta={b && s.vol_ann != null && b.vol_ann != null ? s.vol_ann - b.vol_ann : null} invert deltaFormat={(x) => fmtPct(x, 1, { signed: true })} deltaLabel={`vs ${bench}`} />
      <StatTile label="Sharpe" value={s.sharpe} format={(v) => fmtNum(v, 2)} info={INFO.sharpe_se} caption={si.se_hac != null ? <span className="num">± {fmtNum(si.se_hac, 2)} SE{rfZero ? " · rf 0" : ""}</span> : undefined} />
      <StatTile label="Sortino" value={s.sortino} format={(v) => fmtNum(v, 2)} info="sortino" caption={b ? <span className="num">{bench} {fmtNum(b.sortino, 2)}</span> : undefined} />
      <StatTile label="Max DD" value={s.max_drawdown} format={(v) => fmtPct(v, 1)} tone="loss" info="max_drawdown" caption={s.max_drawdown_trough ? `trough ${fmtDate(s.max_drawdown_trough, "month")}` : undefined} />
      <StatTile label={`Beta to ${bench}`} value={r?.beta ?? null} format={(v) => fmtNum(v, 2)} info="beta" caption={r?.r2 != null ? <span className="num">R² {fmtNum(r.r2, 2)}</span> : undefined} />
      <StatTile label="Alpha (ann.)" value={r?.alpha_ann ?? null} format={(v) => fmtSignedPct(v, 2)} tone="auto" info={INFO.alpha} caption={r?.alpha_t != null ? <span className="num">t = {fmtNum(r.alpha_t, 2)}{Math.abs(r.alpha_t) < 2 ? " · not significant" : ""}</span> : undefined} />
      <StatTile label="Info ratio" value={r?.information_ratio ?? null} format={(v) => fmtNum(v, 2)} tone="auto" info={INFO.information_ratio} caption={r?.tracking_error != null ? <span className="num">TE {fmtPct(r.tracking_error, 1)}</span> : undefined} />
    </StatGrid>
  );
}

// ------------------------------------------------------------------ equity

function EquityChart({ d, notional, logY }: { d: PerformanceOut; notional: number; logY: boolean }) {
  const series = useMemo(() => {
    const x = asDates(d.equity.index);
    const out = [{ name: "Portfolio", x, y: (d.equity.data.portfolio ?? []).map((v) => (v == null ? null : v * notional)), width: 2.2 }];
    if (d.equity.data.benchmark) out.push({ name: d.portfolio.benchmark ?? "Benchmark", x, y: d.equity.data.benchmark.map((v) => (v == null ? null : v * notional)), width: 1.5 });
    return out;
  }, [d, notional]);
  const end = series[0].y[series[0].y.length - 1];
  const bEnd = series[1]?.y[series[1].y.length - 1];
  return (
    <>
      <Reading>
        {fmtCurrency(notional, { compact: true })} invested on {fmtDate(d.portfolio.start)} would be <strong className="num">{fmtCurrency(end, { compact: true, digits: 2 })}</strong> on {fmtDate(d.portfolio.end)}
        {bEnd != null && (
          <>
            , against <span className="num">{fmtCurrency(bEnd, { compact: true, digits: 2 })}</span> in {d.portfolio.benchmark}
          </>
        )}
        .
      </Reading>
      <TimeSeriesChart series={series} yFormat="usd" digits={0} logY={logY} rangeSelector height={330} baseline={notional} />
    </>
  );
}

function RelativeCard({ d, bench }: { d: PerformanceOut; bench: string }) {
  const r = d.relative;
  const s = d.summary;
  const to = d.portfolio.turnover;
  if (!r) return <div className="subtle small">No benchmark selected.</div>;
  return (
    <div className="stack">
      <KV
        rows={[
          { label: "Active return", value: fmtSignedPct(r.active_return_ann, 2), tone: r.active_return_ann != null && r.active_return_ann < 0 ? "loss" : "gain", info: { text: `Annualized mean of the daily return difference versus ${bench}.` } },
          { label: "Tracking error", value: fmtPct(r.tracking_error, 2), info: INFO.tracking_error },
          { label: "Up capture", value: fmtPct(r.up_capture, 0), info: INFO.capture },
          { label: "Down capture", value: fmtPct(r.down_capture, 0), info: INFO.capture, tone: r.down_capture != null && r.up_capture != null && r.down_capture < r.up_capture ? "gain" : "" },
          { label: "Correlation", value: fmtNum(r.correlation, 2), info: INFO.correlation },
          { label: "Treynor", value: fmtPct(r.treynor, 2), info: INFO.treynor },
          { label: "M²", value: fmtPct(r.m2, 2), info: INFO.m2, hint: r.m2_excess != null ? `${fmtSignedPct(r.m2_excess, 2)} vs ${bench}` : undefined },
          { label: "Calmar", value: fmtNum(s.calmar, 2), info: INFO.calmar },
          { label: "Omega", value: fmtNum(s.omega, 2), info: INFO.omega },
          { label: "Ulcer index", value: fmtPct(s.ulcer_index, 1), info: INFO.ulcer },
          ...(to ? [{ label: "Turnover / yr", value: fmtPct(to.annual, 1), info: INFO.turnover, hint: `${to.rebalances} rebalances` }] : []),
        ]}
      />
    </div>
  );
}

// ------------------------------------------------------------------ drawdowns

function DrawdownChart({ d, bench }: { d: PerformanceOut; bench: string }) {
  const x = useMemo(() => asDates(d.drawdown.index), [d]);
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const out: Data[] = [];
      if (d.drawdown.data.benchmark)
        out.push({ type: "scatter", mode: "lines", name: bench, x, y: d.drawdown.data.benchmark, line: { color: t.text3, width: 1, dash: "dot" }, hovertemplate: `<b>${bench}</b> %{y:.1%}<extra></extra>` } as Data);
      out.push({ type: "scatter", mode: "lines", name: "Portfolio", x, y: d.drawdown.data.portfolio, line: { color: t.loss, width: 1.6 }, fill: "tozeroy", fillcolor: withAlpha(t.loss, 0.14), hovertemplate: "<b>Portfolio</b> %{y:.1%}<extra></extra>" } as Data);
      return out;
    },
    [d, x, bench],
  );
  const layout = useMemo(
    () => (t: Tokens) => ({
      hovermode: "x unified",
      showlegend: true,
      margin: { l: 16, r: 8, t: 30, b: 28 },
      xaxis: { type: "date", hoverformat: "%d %b %Y" },
      yaxis: { tickformat: ".0%", side: "right", rangemode: "tozero" },
      shapes: d.drawdowns.map((e) => ({ type: "rect", xref: "x", yref: "paper", x0: e.peak, x1: e.recovery ?? d.portfolio.end, y0: 0, y1: 1, fillcolor: withAlpha(t.warn, 0.08), line: { width: 0 }, layer: "below" })),
      annotations: d.drawdowns.map((e, i) => ({ x: e.trough, xref: "x", y: e.depth, yref: "y", text: `${i + 1}`, showarrow: false, yshift: -10, font: { family: t.fontMono, size: 10, color: t.text2 } })),
    }),
    [d],
  );
  return <Chart data={data} layout={layout as any} height={280} />;
}

function EpisodesTable({ rows }: { rows: DrawdownEpisode[] }) {
  const cols: Column<DrawdownEpisode & { i: number }>[] = [
    { key: "i", label: "#", numeric: true, width: 28 },
    { key: "depth", label: "Depth", numeric: true, format: (v) => fmtPct(v, 1), className: "loss" },
    { key: "peak", label: "Peak", format: (v) => fmtDate(v) },
    { key: "trough", label: "Trough", format: (v) => fmtDate(v) },
    { key: "recovery", label: "Recovered", format: (v) => (v ? fmtDate(v) : "—"), hideBelow: 600 },
    { key: "decline", label: "Decline", numeric: true, format: (v) => `${fmtNum(v, 0)}d`, hideBelow: 900, info: { text: "Trading days from the peak to the trough." } },
    { key: "recovery_periods", label: "Recovery", numeric: true, render: (r) => (r.recovered ? <span className="num">{fmtNum(r.recovery_periods, 0)}d</span> : <span className="badge warn">open</span>), info: { text: "Trading days from the trough back to the previous peak. 'open' = not yet recovered." } },
    { key: "length", label: "Length", numeric: true, format: (v) => `${fmtNum(v, 0)}d`, hideBelow: 900, info: { text: "Trading days from peak to recovery (or to today if still open)." } },
  ];
  return <DataTable rows={rows.map((r, i) => ({ ...r, i: i + 1 }))} columns={cols} rowKey={(r) => r.peak} />;
}

// ------------------------------------------------------------------ monthly

type MonthRow = Record<string, number | null> & { year: number };

function MonthlyTable({ d }: { d: PerformanceOut }) {
  const rows = useMemo(() => [...(d.monthly_table.rows as MonthRow[])].sort((a, b) => b.year - a.year), [d]);
  const vals = rows.flatMap((r) => MONTHS.map((m) => r[m])).filter((v): v is number => typeof v === "number");
  const scale = Math.max(0.03, ...vals.map((v) => Math.abs(v))) * 0.9;
  const cols: Column<MonthRow>[] = [
    { key: "year", label: "Year", render: (r) => <strong className="num">{r.year}</strong>, width: 56 },
    ...MONTHS.map<Column<MonthRow>>((m) => ({ key: m, label: m, numeric: true, format: (v) => (v == null ? "" : fmtPct(v, 1)), heat: { min: -scale, max: scale }, sortable: false })),
    { key: "Annual", label: "Year", numeric: true, format: (v) => <strong>{fmtPct(v, 1)}</strong>, color: "sign", info: { text: "Compounded return of the months shown for that year (partial years are partial)." } },
  ];
  return <DataTable rows={rows} columns={cols} rowKey={(r) => r.year} className="pl-monthly" />;
}

// ------------------------------------------------------------------ rolling

function RollingGrid({ d, bench }: { d: PerformanceOut; bench: string }) {
  if (!d.rolling) return <div className="subtle small">History is shorter than the {d.rolling_window}-day window.</div>;
  const x = asDates(d.rolling.index);
  const col = (k: string) => d.rolling!.data[k] ?? [];
  const charts: { title: string; key: string; fmt: "pct" | "num"; base?: number; info: string }[] = [
    { title: "Sharpe", key: "sharpe", fmt: "num", base: 0, info: "Above 0: beat cash over the trailing year." },
    { title: "Volatility", key: "vol", fmt: "pct", info: "Annualized standard deviation over the trailing year." },
    { title: `Beta to ${bench}`, key: "beta", fmt: "num", base: 1, info: "1 = moves one-for-one with the benchmark." },
    { title: "Return", key: "return", fmt: "pct", base: 0, info: "Trailing one-year compounded return." },
  ];
  return (
    <div className="pl-small-multiples">
      {charts.map((c, i) => (
        <div key={c.key} className="pl-sm">
          <div className="pl-sm-head">
            <span className="pl-sm-title">{c.title}</span>
            <span className="subtle small">{c.info}</span>
          </div>
          <TimeSeriesChart series={[{ name: c.title, x, y: col(c.key), color: `var(--c${[1, 2, 3, 5][i]})` }]} yFormat={c.fmt} digits={2} height={190} baseline={c.base} showLegend={false} />
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ distribution

function Distribution({ d }: { d: PerformanceOut }) {
  const s = d.summary;
  const h = d.returns_histogram;
  const centers = h.edges.slice(0, -1).map((e, i) => (e + h.edges[i + 1]) / 2);
  const data = useMemo(
    () => (t: Tokens): Data[] => [
      { type: "bar", x: centers, y: h.counts, marker: { color: centers.map((c) => (s.var != null && c <= -s.var ? t.loss : t.categorical[0])), opacity: 0.85 }, hovertemplate: "%{x:.2%}: <b>%{y} days</b><extra></extra>" } as Data,
    ],
    [d], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const layout = useMemo(
    () => (t: Tokens) => ({
      bargap: 0.05,
      showlegend: false,
      margin: { l: 8, r: 8, t: 20, b: 30 },
      xaxis: { tickformat: ".1%", showspikes: false },
      yaxis: { title: { text: "days" }, side: "right" },
      shapes: [s.var, s.cvar].filter((v): v is number => v != null).map((v, i) => ({ type: "line", yref: "paper", y0: 0, y1: 1, x0: -v, x1: -v, line: { color: t.loss, width: 1.2, dash: i ? "dot" : "dash" } })),
      annotations: [
        s.var != null ? { x: -s.var, yref: "paper", y: 1, text: `VaR ${fmtPct(s.var_level, 0)}  ${fmtPct(-s.var, 2)}`, showarrow: false, xanchor: "left", yanchor: "bottom", xshift: 4, font: { size: 11, color: t.text2 } } : null,
        s.cvar != null ? { x: -s.cvar, yref: "paper", y: 0.88, text: `CVaR ${fmtPct(-s.cvar, 2)}`, showarrow: false, xanchor: "right", yanchor: "bottom", xshift: -4, font: { size: 11, color: t.text2 } } : null,
      ].filter(Boolean),
    }),
    [s],
  );
  return (
    <>
      <Chart data={data} layout={layout as any} height={220} />
      <KV
        cols={2}
        rows={[
          { label: "Skewness", value: fmtNum(s.skew, 2), info: INFO.skew, tone: (s.skew ?? 0) < 0 ? "loss" : "" },
          { label: "Excess kurtosis", value: fmtNum(s.excess_kurtosis, 1), info: INFO.kurtosis },
          { label: "Hit rate", value: fmtPct(s.hit_rate, 1), info: { text: "Share of days with a positive return." } },
          { label: "Payoff ratio", value: fmtMultiple(s.payoff_ratio, 2), info: { text: "Average winning day divided by average losing day (in absolute value)." } },
          { label: "Best day", value: fmtSignedPct(s.best_day, 2), tone: "gain", hint: fmtDate(s.best_day_date) },
          { label: "Worst day", value: fmtSignedPct(s.worst_day, 2), tone: "loss", hint: fmtDate(s.worst_day_date) },
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
  const conf = si.min_track_record_confidence;
  const max = Math.max(years, need != null && Number.isFinite(need) ? need : 0) * 1.1 || 1;
  const hurdle = si.psr_benchmark_sharpe;
  const psr = si.psr;
  const verdict =
    psr == null
      ? "The probabilistic Sharpe ratio could not be computed for this record."
      : psr >= conf
        ? `There is a ${fmtPct(psr, 1)} probability that the true Sharpe ratio exceeds ${fmtNum(hurdle, 2)} — above the ${fmtPct(conf, 0)} bar, so the record is long enough to rule out luck at that confidence.`
        : `There is only a ${fmtPct(psr, 1)} probability that the true Sharpe ratio exceeds ${fmtNum(hurdle, 2)} — below the ${fmtPct(conf, 0)} bar, so this record cannot yet rule out luck.`;
  return (
    <div className="stack">
      <div className="pl-psr">
        <div className="pl-psr-big">
          <span className="eyebrow">PSR vs SR* = {fmtNum(hurdle, 2)}</span>
          <span className={`num pl-psr-value ${psr != null && psr >= conf ? "gain" : "warn"}`}>{fmtPct(psr, 1)}</span>
        </div>
        <p className="pl-psr-text">{verdict}</p>
      </div>
      <div className="pl-trl" aria-label="Track record versus minimum track record length">
        <div className="pl-trl-head">
          <span>
            Track record <strong className="num">{fmtNum(years, 1)} y</strong>
          </span>
          <span>
            Needed at {fmtPct(conf, 0)} <strong className="num">{need != null && Number.isFinite(need) ? `${fmtNum(need, 1)} y` : "never (SR ≤ hurdle)"}</strong>
          </span>
        </div>
        <div className="pl-trl-track">
          <div className={`pl-trl-fill ${si.track_record_sufficient ? "ok" : "short"}`} style={{ width: `${Math.min(100, (years / max) * 100)}%` }} />
          {need != null && Number.isFinite(need) && <div className="pl-trl-mark" style={{ left: `${Math.min(100, (need / max) * 100)}%` }} title={`MinTRL ${fmtNum(need, 2)} years`} />}
        </div>
      </div>
      <KV
        cols={2}
        rows={[
          { label: "SE (IID normal)", value: fmtNum(si.se_iid, 3), info: INFO.sharpe_se },
          { label: "SE (fat tails)", value: fmtNum(si.se_nonnormal, 3), info: { text: "Mertens (2002) correction for skewness and kurtosis." } },
          { label: "SE (HAC)", value: fmtNum(si.se_hac, 3), info: { text: "Newey–West GMM standard error robust to autocorrelation (Lo 2002)." } },
          { label: "95% CI", value: `${fmtNum(si.ci95_lower, 2)} – ${fmtNum(si.ci95_upper, 2)}`, info: { text: "Sharpe ± 1.96 HAC standard errors." } },
          { label: "t-stat (HAC)", value: fmtNum(si.t_stat_hac, 2), info: INFO.sharpe_se },
          { label: "MinTRL", value: need != null && Number.isFinite(need) ? `${fmtNum(need, 1)} y` : "—", info: INFO.mintrl },
        ]}
      />
    </div>
  );
}
