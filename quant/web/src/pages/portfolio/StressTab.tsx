/**
 * Stress — historical crisis replays (POST /risk/stress/historical) as one ruled table, the
 * selected replay's path and positions, and a conditional shock builder
 * (POST /risk/stress/conditional, Kupiec 1998).
 */
import { useMemo, useState } from "react";
import { BarChart, DataTable, Field, NumberField, Panel, Section, SegmentedControl, StatGrid, StatTile, TickerInput, TimeSeriesChart, type Column } from "../../components";
import { fmtDate, fmtNum, fmtPct, fmtSignedPct, signClass } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import type { PortfolioIn } from "../../lib/types";
import { INFO } from "./info";
import { KV, Readline, RunButton, useCommitted, usd } from "./shared";
import type { ConditionalPosition, ConditionalStressOut, HistoricalStressOut, ScenarioPosition, ScenarioResult } from "./types";

export function StressTab({ req }: { req: PortfolioIn }) {
  return (
    <>
      <HistoricalSection req={req} />
      <ConditionalSection req={req} />
    </>
  );
}

// =================================================================== historical

function HistoricalSection({ req }: { req: PortfolioIn }) {
  const q = useApiPost<HistoricalStressOut>("/risk/stress/historical", req);
  const [picked, setPicked] = useState<string | null>(null);
  const scen = q.data?.scenarios ?? [];
  const complete = scen.filter((s) => s.complete);
  const incomplete = scen.filter((s) => !s.complete);
  const worst = complete.reduce<ScenarioResult | null>((a, b) => (a == null || (b.portfolio_return ?? 0) < (a.portfolio_return ?? 0) ? b : a), null);
  const sel = scen.find((s) => s.id === picked) ?? worst ?? null;
  const bench = req.benchmark || "SPY";
  const cols: Column<ScenarioResult>[] = [
    { key: "name", label: "Scenario", render: (s) => <span className="pl-model-name">{s.name}</span> },
    { key: "category", label: "Type", format: (v) => String(v ?? "").toUpperCase(), hideBelow: 900 },
    { key: "start", label: "Start", format: (v) => fmtDate(v, "iso"), hideBelow: 600 },
    { key: "sessions", label: "Days", numeric: true, format: (v) => fmtNum(v, 0), hideBelow: 900 },
    { key: "portfolio_return", label: "Port", numeric: true, format: (v) => fmtSignedPct(v, 1), color: "sign" },
    { key: "pnl_usd", label: "P&L", numeric: true, format: (v) => usd(v, true), color: "sign", hideBelow: 600 },
    { key: "benchmark_return", label: bench, numeric: true, format: (v) => fmtSignedPct(v, 1), color: "sign" },
    { key: "max_drawdown", label: "Max DD", numeric: true, format: (v) => fmtPct(v, 1), className: "loss", info: "max_drawdown", hideBelow: 900 },
    { key: "proxied", label: "Proxy", value: (s) => s.proxied.length, render: (s) => (s.proxied.length ? <span className="num" title="No real prices in the window: beta × benchmark">{s.proxied.join(" ")}</span> : "—"), hideBelow: 1200 },
  ];
  return (
    <Section title={`Stress · replays · vs ${bench}`}>
      <Panel<HistoricalStressOut> title={q.data ? `Crises · ${complete.length}/${scen.length} replayable` : "Crises"} info={INFO.hist_stress} query={q} flush skeletonHeight={300}>
        {() => (
          <>
            <DataTable rows={complete} columns={cols} rowKey={(s) => s.id} onRowClick={(s) => setPicked(s.id)} isActive={(s) => s.id === sel?.id} defaultSort={{ key: "portfolio_return", dir: "asc" }} compact />
            {incomplete.length > 0 && (
              <div className="pl-foot num">
                NOT REPLAYABLE · NO PRICE HISTORY · {incomplete.length} · {incomplete.map((s) => s.name).join(" · ")}
              </div>
            )}
          </>
        )}
      </Panel>
      {sel && sel.complete && (
        <div className="grid-3">
          <Panel title={`${sel.name} · path`} span={2} notes={sel.notes} asOf={sel.end ?? undefined}>
            <ScenarioPath s={sel} />
          </Panel>
          <Panel title={`${sel.name} · holdings`} flush asOf={sel.end ?? undefined}>
            <ScenarioPositions s={sel} />
          </Panel>
        </div>
      )}
    </Section>
  );
}

function ScenarioPath({ s }: { s: ScenarioResult }) {
  const p = s.path!;
  const series = useMemo(
    () => [
      { name: "PORT", x: p.dates, y: p.portfolio, color: "var(--ink)", width: 1.5 },
      ...(p.benchmark ? [{ name: s.benchmark, x: p.dates, y: p.benchmark, color: "var(--ink-2)", dash: "dot" as const }] : []),
    ],
    [p, s.benchmark],
  );
  return (
    <>
      <StatGrid min={130}>
        <StatTile label="Port" value={s.portfolio_return} format={(v) => fmtSignedPct(v, 2)} tone="auto" caption={<span className="num">{usd(s.pnl_usd, true)}</span>} />
        <StatTile label={s.benchmark} value={s.benchmark_return} format={(v) => fmtSignedPct(v, 2)} tone="auto" />
        <StatTile label="Max DD" value={s.max_drawdown} format={(v) => fmtPct(v, 2)} tone="loss" info="max_drawdown" />
        {s.worst_day && <StatTile label="Worst day" value={s.worst_day.return} format={(v) => fmtSignedPct(v, 2)} tone="loss" caption={<span className="num">{fmtDate(s.worst_day.date, "iso")}</span>} />}
      </StatGrid>
      <TimeSeriesChart series={series} yFormat="pct" digits={2} height={280} baseline={0} />
    </>
  );
}

function ScenarioPositions({ s }: { s: ScenarioResult }) {
  const cols: Column<ScenarioPosition>[] = [
    {
      key: "ticker",
      label: "Holding",
      render: (r) => (
        <span className="pl-model">
          <span className="num">{r.ticker}</span>
          {r.source === "proxy" && (
            <span className="pl-tag" title={`beta ${fmtNum(r.beta, 2)} × ${s.benchmark} (R² ${fmtNum(r.beta_r2, 2)}, ${r.beta_obs} sessions)`}>
              PROXY
            </span>
          )}
          {r.source === "missing" && <span className="pl-tag">NO DATA</span>}
        </span>
      ),
    },
    { key: "weight", label: "Weight", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 600 },
    { key: "return", label: "Return", numeric: true, format: (v) => fmtSignedPct(v, 1), color: "sign" },
    { key: "contribution", label: "Contrib", numeric: true, format: (v) => fmtSignedPct(v, 2), color: "sign" },
    { key: "pnl_usd", label: "P&L", numeric: true, format: (v) => usd(v, true), color: "sign" },
  ];
  return <DataTable rows={s.positions} columns={cols} rowKey={(r) => r.ticker} defaultSort={{ key: "contribution", dir: "asc" }} compact />;
}

// =================================================================== conditional

type Shock = { ticker: string; shock: number };

const PRESETS: { label: string; shocks: Shock[] }[] = [
  { label: "SPY −10", shocks: [{ ticker: "SPY", shock: -0.1 }] },
  { label: "QQQ −15", shocks: [{ ticker: "QQQ", shock: -0.15 }] },
  { label: "TLT −8", shocks: [{ ticker: "TLT", shock: -0.08 }] },
  { label: "SPY −10 TLT −8", shocks: [{ ticker: "SPY", shock: -0.1 }, { ticker: "TLT", shock: -0.08 }] },
  { label: "GLD +10", shocks: [{ ticker: "GLD", shock: 0.1 }] },
];

function ConditionalSection({ req }: { req: PortfolioIn }) {
  const [shocks, setShocks] = useState<Shock[]>([{ ticker: (req.benchmark || "SPY").toUpperCase(), shock: -0.1 }]);
  const [cov, setCov] = useState<"ewma" | "sample">("ewma");
  const draft = useMemo(() => ({ ...req, shocks: Object.fromEntries(shocks.filter((s) => s.ticker).map((s) => [s.ticker, s.shock])), cov_method: cov }), [req, shocks, cov]);
  const { committed, run, dirty } = useCommitted(draft);
  const q = useApiPost<ConditionalStressOut>("/risk/stress/conditional", committed, { enabled: Object.keys(committed.shocks).length > 0 });
  const set = (i: number, patch: Partial<Shock>) => setShocks(shocks.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  const tickers = [...new Set([...req.holdings.map((h) => h.ticker), req.benchmark || "SPY"])];

  return (
    <Section title="Stress · conditional">
      <div className="grid-3">
        <Panel title="Shocks" info={INFO.cond_stress} actions={<RunButton onRun={run} dirty={dirty} busy={q.isFetching} disabled={!shocks.some((s) => s.ticker)} />}>
          <div className="stack">
            <div className="pl-btnrow">
              {PRESETS.map((p) => (
                <button key={p.label} type="button" className="btn btn-sm" onClick={() => setShocks(p.shocks)}>
                  {p.label}
                </button>
              ))}
            </div>
            <div className="pl-shocks">
              {shocks.map((s, i) => (
                <div className="pl-shock" key={i}>
                  <select className="select" aria-label="Shocked ticker" value={tickers.includes(s.ticker) ? s.ticker : "__other"} onChange={(e) => e.target.value !== "__other" && set(i, { ticker: e.target.value })}>
                    {tickers.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                    {!tickers.includes(s.ticker) && <option value="__other">{s.ticker}</option>}
                  </select>
                  <NumberField value={s.shock} onChange={(v) => set(i, { shock: v })} percent min={-1} max={5} step={0.01} digits={1} width={100} ariaLabel={`${s.ticker} shock`} />
                  <button type="button" className="btn btn-sm" aria-label={`DEL ${s.ticker}`} onClick={() => setShocks(shocks.filter((_, j) => j !== i))} disabled={shocks.length <= 1}>
                    DEL
                  </button>
                </div>
              ))}
            </div>
            <Field label="Add ticker">
              <TickerInput onSelect={(t: string) => !shocks.some((s) => s.ticker === t) && shocks.length < 20 && setShocks([...shocks, { ticker: t.toUpperCase(), shock: -0.05 }])} />
            </Field>
            <Field label="Covariance">
              <SegmentedControl size="sm" ariaLabel="Covariance" options={[{ value: "ewma", label: "EWMA" }, { value: "sample", label: "SAMPLE" }]} value={cov} onChange={setCov} />
            </Field>
          </div>
        </Panel>
        <Panel<ConditionalStressOut> span={2} title="Conditional P&L" query={q} skeletonHeight={320}>
          {(d) => <ConditionalResult d={d} />}
        </Panel>
      </div>
    </Section>
  );
}

function ConditionalResult({ d }: { d: ConditionalStressOut }) {
  const rows = [...d.positions].sort((a, b) => a.pnl_usd - b.pnl_usd);
  const cols: Column<ConditionalPosition>[] = [
    { key: "ticker", label: "Holding", render: (r) => <span className="pl-model"><span className="num">{r.ticker}</span>{r.shocked && <span className="pl-tag">SHOCKED</span>}</span> },
    { key: "weight", label: "Weight", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 600 },
    { key: "move", label: "Move", numeric: true, format: (v) => fmtSignedPct(v, 2), color: "sign" },
    { key: "move_in_sd", label: "σ", numeric: true, format: (v) => fmtNum(v, 1), info: { text: "The move in daily standard deviations; beyond ~5 the linear estimate is fragile." }, hideBelow: 900 },
    { key: "pnl", label: "Contrib", numeric: true, format: (v) => fmtSignedPct(v, 2), color: "sign", hideBelow: 600 },
    { key: "pnl_usd", label: "P&L", numeric: true, format: (v) => usd(v, true), color: "sign" },
  ];
  return (
    <div className="stack">
      <Readline
        items={[
          ...Object.entries(d.shocks).map(([t, s]) => ({ k: t, v: fmtSignedPct(s, 0) })),
          { k: "PORT", v: fmtSignedPct(d.portfolio_return, 2), tone: signClass(d.portfolio_return) === "loss" ? "loss" : "" },
          { k: "P&L", v: usd(d.pnl_usd, true), tone: signClass(d.pnl_usd) === "loss" ? "loss" : "" },
        ]}
      />
      <div className="grid-2">
        <BarChart horizontal colorBySign x={rows.map((r) => r.ticker)} y={rows.map((r) => r.pnl_usd)} yFormat="usd" digits={0} height={Math.max(160, rows.length * 26 + 12)} />
        <KV
          rows={[
            { label: "Expected P&L", value: usd(d.pnl_usd, true), tone: signClass(d.pnl_usd) === "loss" ? "loss" : "" },
            { label: "Residual vol 1D", value: fmtPct(d.conditional_residual_vol_daily, 2), info: { text: "Conditional standard deviation of the portfolio once the shocked assets are known." } },
            ...Object.entries(d.shock_zscores).map(([t, z]) => ({ label: `${t} shock σ`, value: fmtNum(z, 1), info: { text: "The shock in daily standard deviations of that asset." } })),
          ]}
        />
      </div>
      <DataTable rows={d.positions} columns={cols} rowKey={(r) => r.ticker} defaultSort={{ key: "pnl_usd", dir: "asc" }} compact />
    </div>
  );
}
