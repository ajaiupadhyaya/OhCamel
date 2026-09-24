/**
 * Stress — historical crisis replays (GET /risk/scenarios + POST /risk/stress/historical)
 * and a conditional shock builder (POST /risk/stress/conditional, Kupiec 1998).
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, Field, Icon, NumberField, Panel, Section, SegmentedControl, StatGrid, StatTile, TickerInput, type Column } from "../../components";
import { Bars } from "./Bars";
import { fmtDate, fmtNum, fmtPct, fmtSignedPct, signClass } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import type { PortfolioIn } from "../../lib/types";
import { INFO } from "./info";
import { KV, Reading, RunButton, useCommitted, usd } from "./shared";
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
  const [showIncomplete, setShowIncomplete] = useState(false);
  const scen = q.data?.scenarios ?? [];
  const complete = scen.filter((s) => s.complete);
  const incomplete = scen.filter((s) => !s.complete);
  const worst = complete.reduce<ScenarioResult | null>((a, b) => (a == null || (b.portfolio_return ?? 0) < (a.portfolio_return ?? 0) ? b : a), null);
  const sel = scen.find((s) => s.id === picked) ?? worst ?? null;
  const bench = req.benchmark || "SPY";
  return (
    <Section
      title="Replaying past crises"
      description={`What today's weights would have lost if each historical crisis happened again, using the real prices of every holding over that window — next to ${bench}. Click a scenario to see its path.`}
    >
      <Panel<HistoricalStressOut>
        title="Crisis scenarios"
        subtitle={q.data ? `${complete.length} of ${scen.length} scenarios can be replayed with the available price history; the rest are listed below with the reason.` : undefined}
        info={INFO.hist_stress}
        query={q}
        skeletonHeight={300}
      >
        {() => (
          <>
            {worst && (
              <Reading>
                The worst replay is {worst.name} ({fmtDate(worst.start, "month")}): {fmtSignedPct(worst.portfolio_return, 1)}, or {usd(worst.pnl_usd, true)}
                {worst.benchmark_return != null && <> — against {fmtSignedPct(worst.benchmark_return, 1)} for {bench}</>}.
              </Reading>
            )}
            <div className="pl-scen-grid">
              {complete.map((s) => (
                <ScenarioCard key={s.id} s={s} active={sel?.id === s.id} onClick={() => setPicked(s.id)} />
              ))}
            </div>
            {incomplete.length > 0 && (
              <div className="pl-incomplete">
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowIncomplete((v) => !v)} aria-expanded={showIncomplete}>
                  <Icon name={showIncomplete ? "chevron-up" : "chevron-down"} size={14} />
                  {incomplete.length} scenario{incomplete.length > 1 ? "s" : ""} cannot be replayed
                </button>
                {showIncomplete && (
                  <ul className="pl-incomplete-list">
                    {incomplete.map((s) => (
                      <li key={s.id}>
                        <span className="badge unknown">incomplete</span>
                        <strong>{s.name}</strong>
                        <span className="subtle num small">
                          {fmtDate(s.start)} – {fmtDate(s.end)}
                        </span>
                        <span className="subtle small">{s.notes[0] ?? `No prices for ${s.missing.join(", ")} in this window.`}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </>
        )}
      </Panel>
      {sel && sel.complete && (
        <div className="grid-3">
          <Panel title={sel.name} subtitle={sel.description} span={2} info={{ text: "Cumulative return from the base close through each session of the window, portfolio vs benchmark." }} notes={sel.notes}>
            <ScenarioPath s={sel} />
          </Panel>
          <Panel title="By holding" subtitle="Each position's return over the window and what it contributed." flush>
            <ScenarioPositions s={sel} />
          </Panel>
        </div>
      )}
      {complete.length > 1 && (
        <Panel title="All replays at a glance" subtitle={`Portfolio vs ${bench} return in every complete scenario. Where the portfolio bar is shorter, diversification did its job.`} loading={q.isLoading}>
          <Bars
            series={[
              { name: "Portfolio", x: complete.map((s) => s.name), y: complete.map((s) => s.portfolio_return) },
              { name: bench, x: complete.map((s) => s.name), y: complete.map((s) => s.benchmark_return) },
            ]}
            yFormat="pct"
            digits={1}
            horizontal
            height={complete.length * 46 + 60}
          />
        </Panel>
      )}
    </Section>
  );
}

function ScenarioCard({ s, active, onClick }: { s: ScenarioResult; active: boolean; onClick: () => void }) {
  return (
    <button type="button" className={`pl-scen ${active ? "active" : ""}`} onClick={onClick} aria-pressed={active}>
      <div className="pl-scen-head">
        <span className="pl-scen-name">{s.name}</span>
        <span className="pl-scen-cat">{s.category}</span>
      </div>
      <div className="pl-scen-dates num">
        {fmtDate(s.start)} – {fmtDate(s.end)}
        {s.sessions ? ` · ${s.sessions} sessions` : ""}
      </div>
      <div className="pl-scen-body">
        <span className={`num pl-scen-ret ${signClass(s.portfolio_return)}`}>{fmtSignedPct(s.portfolio_return, 1)}</span>
        <span className={`num pl-scen-usd ${signClass(s.pnl_usd)}`}>{usd(s.pnl_usd, true)}</span>
      </div>
      <div className="pl-scen-foot">
        <span>
          {s.benchmark} <span className={`num ${signClass(s.benchmark_return)}`}>{fmtSignedPct(s.benchmark_return, 1)}</span>
        </span>
        <span>
          max DD <span className="num loss">{fmtPct(s.max_drawdown, 1)}</span>
        </span>
      </div>
      {s.proxied.length > 0 && (
        <div className="pl-scen-flag">
          <span className="badge warn" title="No real prices in the window: proxied by beta × benchmark">
            proxied: {s.proxied.join(", ")}
          </span>
        </div>
      )}
    </button>
  );
}

function ScenarioPath({ s }: { s: ScenarioResult }) {
  const p = s.path!;
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const out: Data[] = [{ type: "scatter", mode: p.dates.length > 40 ? "lines" : "lines+markers", name: "Portfolio", x: p.dates, y: p.portfolio, line: { color: t.categorical[0], width: 2.2 }, marker: { size: 5 }, fill: "tozeroy", fillcolor: "rgba(0,0,0,0)", hovertemplate: "<b>Portfolio</b> %{y:.2%}<extra></extra>" } as Data];
      if (p.benchmark) out.push({ type: "scatter", mode: "lines", name: s.benchmark, x: p.dates, y: p.benchmark, line: { color: t.text3, width: 1.4, dash: "dot" }, hovertemplate: `<b>${s.benchmark}</b> %{y:.2%}<extra></extra>` } as Data);
      return out;
    },
    [p, s.benchmark],
  );
  const layout = useMemo(() => ({ hovermode: "x unified", margin: { l: 16, r: 8, t: 30, b: 28 }, xaxis: { type: "date", hoverformat: "%a %d %b %Y" }, yaxis: { tickformat: ".0%", side: "right", zeroline: true }, showlegend: true }), []);
  return (
    <>
      <StatGrid min={130}>
        <StatTile label="Portfolio" value={s.portfolio_return} format={(v) => fmtSignedPct(v, 2)} tone="auto" caption={<span className="num">{usd(s.pnl_usd, true)}</span>} />
        <StatTile label={s.benchmark} value={s.benchmark_return} format={(v) => fmtSignedPct(v, 2)} tone="auto" />
        <StatTile label="Max DD in window" value={s.max_drawdown} format={(v) => fmtPct(v, 2)} tone="loss" info="max_drawdown" />
        {s.worst_day && <StatTile label="Worst day" value={s.worst_day.return} format={(v) => fmtSignedPct(v, 2)} tone="loss" caption={fmtDate(s.worst_day.date)} />}
      </StatGrid>
      <Chart data={data} layout={layout as any} height={280} />
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
          <strong className="num">{r.ticker}</strong>
          {r.source === "proxy" && (
            <span className="badge warn" title={`Proxied: beta ${fmtNum(r.beta, 2)} × ${s.benchmark} (R² ${fmtNum(r.beta_r2, 2)}, ${r.beta_obs} sessions)`}>
              proxy
            </span>
          )}
          {r.source === "missing" && <span className="badge unknown">no data</span>}
        </span>
      ),
    },
    { key: "weight", label: "Weight", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 600 },
    { key: "return", label: "Return", numeric: true, format: (v) => fmtSignedPct(v, 1), color: "sign" },
    { key: "contribution", label: "Contrib.", numeric: true, format: (v) => fmtSignedPct(v, 2), color: "sign" },
    { key: "pnl_usd", label: "P&L", numeric: true, format: (v) => usd(v, true), color: "sign" },
  ];
  return <DataTable rows={s.positions} columns={cols} rowKey={(r) => r.ticker} defaultSort={{ key: "contribution", dir: "asc" }} />;
}

// =================================================================== conditional

type Shock = { ticker: string; shock: number };

const PRESETS: { label: string; title: string; shocks: Shock[] }[] = [
  { label: "Equities −10%", title: "S&P 500 ETF falls 10%", shocks: [{ ticker: "SPY", shock: -0.1 }] },
  { label: "Tech −15%", title: "Nasdaq-100 ETF falls 15%", shocks: [{ ticker: "QQQ", shock: -0.15 }] },
  { label: "Long bonds −8%", title: "20Y+ Treasury ETF falls 8% (rates up)", shocks: [{ ticker: "TLT", shock: -0.08 }] },
  { label: "Stocks & bonds down", title: "The 2022 pattern: equities and Treasuries fall together", shocks: [{ ticker: "SPY", shock: -0.1 }, { ticker: "TLT", shock: -0.08 }] },
  { label: "Gold +10%", title: "Gold ETF rallies 10%", shocks: [{ ticker: "GLD", shock: 0.1 }] },
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
    <Section
      title="What if…?"
      description="Choose a move for one or more assets. Everything else moves by what history says it usually does when those assets move that much — the conditional expectation from the covariance matrix."
    >
      <div className="grid-3">
        <Panel
          title="Shock builder"
          subtitle="Moves are total returns over the shock (e.g. −10%). Presets are starting points; edit freely."
          info={INFO.cond_stress}
          actions={<RunButton onRun={run} dirty={dirty} busy={q.isFetching} label="Run stress" disabled={!shocks.some((s) => s.ticker)} />}
        >
          <div className="stack">
            <div className="row-wrap">
              {PRESETS.map((p) => (
                <button key={p.label} type="button" className="btn btn-sm" title={p.title} onClick={() => setShocks(p.shocks)}>
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
                  <NumberField value={s.shock} onChange={(v) => set(i, { shock: v })} percent min={-1} max={5} step={0.01} digits={1} width={100} />
                  <button type="button" className="icon-btn" aria-label={`Remove ${s.ticker}`} onClick={() => setShocks(shocks.filter((_, j) => j !== i))} disabled={shocks.length <= 1}>
                    <Icon name="x" size={14} />
                  </button>
                </div>
              ))}
            </div>
            <Field label="Add another shocked asset" hint="Any ticker with daily prices — it need not be a holding.">
              <TickerInput onSelect={(t: string) => !shocks.some((s) => s.ticker === t) && shocks.length < 20 && setShocks([...shocks, { ticker: t.toUpperCase(), shock: -0.05 }])} />
            </Field>
            <Field label="Covariance" info={{ text: "EWMA (λ = 0.94) reflects how assets co-move right now; the sample covariance averages the whole window, including calmer or more stressed periods." }}>
              <SegmentedControl size="sm" ariaLabel="Covariance" options={[{ value: "ewma", label: "EWMA (recent)" }, { value: "sample", label: "Full sample" }]} value={cov} onChange={setCov} />
            </Field>
          </div>
        </Panel>
        <Panel<ConditionalStressOut> span={2} title="Conditional P&L" subtitle="Dark bars are the moves you set; light bars are implied moves of the other holdings, scaled by weight into dollars." query={q} skeletonHeight={320}>
          {(d) => <ConditionalResult d={d} />}
        </Panel>
      </div>
    </Section>
  );
}

function ConditionalResult({ d }: { d: ConditionalStressOut }) {
  const rows = [...d.positions].sort((a, b) => a.pnl_usd - b.pnl_usd);
  const data = useMemo(
    () => (t: Tokens): Data[] => [
      {
        type: "bar",
        orientation: "h",
        x: rows.map((r) => r.pnl_usd),
        y: rows.map((r) => r.ticker),
        marker: { color: rows.map((r) => (r.pnl_usd < 0 ? t.loss : t.gain)), opacity: rows.map((r) => (r.shocked ? 1 : 0.55)), line: { width: 0 } },
        customdata: rows.map((r) => [r.move, r.shocked ? "shocked" : "implied"]),
        hovertemplate: "<b>%{y}</b> %{customdata[1]} move %{customdata[0]:+.2%}<br>P&L <b>%{x:$,.0f}</b><extra></extra>",
      } as Data,
    ],
    [rows],
  );
  const cols: Column<ConditionalPosition>[] = [
    { key: "ticker", label: "Holding", render: (r) => <span className="pl-model"><strong className="num">{r.ticker}</strong>{r.shocked && <span className="badge accent">shocked</span>}</span> },
    { key: "weight", label: "Weight", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 600 },
    { key: "move", label: "Move", numeric: true, format: (v) => fmtSignedPct(v, 2), color: "sign" },
    { key: "move_in_sd", label: "σ-days", numeric: true, format: (v) => fmtNum(v, 1), info: { text: "The move expressed in daily standard deviations. Beyond ~5 the linear relationship estimated in normal times may not hold." }, hideBelow: 900 },
    { key: "pnl", label: "Contrib.", numeric: true, format: (v) => fmtSignedPct(v, 2), color: "sign", hideBelow: 600 },
    { key: "pnl_usd", label: "P&L", numeric: true, format: (v) => usd(v, true), color: "sign" },
  ];
  const shockTxt = Object.entries(d.shocks)
    .map(([t, s]) => `${t} ${fmtSignedPct(s, 0)}`)
    .join(" and ");
  return (
    <div className="stack">
      <Reading>
        If {shockTxt}, the portfolio would be expected to move <strong className={`num ${signClass(d.portfolio_return)}`}>{fmtSignedPct(d.portfolio_return, 2)}</strong> — <span className={`num ${signClass(d.pnl_usd)}`}>{usd(d.pnl_usd, true)}</span>.
      </Reading>
      <div className="grid-2">
        <Chart data={data} layout={{ margin: { l: 8, r: 16, t: 8, b: 28 }, xaxis: { tickformat: "$,.2~s", zeroline: true, showspikes: false }, yaxis: { type: "category", showgrid: false, automargin: true }, showlegend: false, hovermode: "closest" } as any} height={Math.max(200, rows.length * 34 + 40)} />
        <KV
          rows={[
            { label: "Expected P&L", value: usd(d.pnl_usd, true), tone: signClass(d.pnl_usd) },
            { label: "Residual vol (1 day)", value: fmtPct(d.conditional_residual_vol_daily, 2), info: { text: "Uncertainty left around the expected P&L once the shocked assets are known: the portfolio's conditional standard deviation." } },
            ...Object.entries(d.shock_zscores).map(([t, z]) => ({ label: `${t} shock size`, value: `${fmtNum(z, 1)} σ`, tone: Math.abs(z) > 10 ? "warn" : "", info: { text: "The shock in daily standard deviations of that asset." } })),
          ]}
        />
      </div>
      <DataTable rows={d.positions} columns={cols} rowKey={(r) => r.ticker} defaultSort={{ key: "pnl_usd", dir: "asc" }} />
    </div>
  );
}
