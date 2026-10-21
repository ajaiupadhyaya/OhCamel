/**
 * ALLOCATION: the optimiser's weights and their ex-ante figures, one ruled table of weight,
 * trade, risk share and the inputs the optimiser saw, the HRP / HERC tree with its bisection,
 * and the expected returns with their standard errors. Ex-ante figures come from the data
 * that chose the weights; the out-of-sample evidence is on BACKTEST · 1/N.
 */
import { useMemo, type CSSProperties } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { DataTable, Panel, StatGrid, StatTile, type Column } from "../../components";
import { CoefChart } from "../../charts/CoefChart";
import { DendroChart } from "../../charts/DendroChart";
import { Note } from "../../design";
import type { ApiError } from "../../lib/api";
import { fmtNum, fmtPct } from "../../lib/format";
import { currentInUniverse } from "./config";
import { COV_CODE, INFO, METHOD_CODE, METHOD_DOC, RETURNS_CODE } from "./info";
import { BarCell, QPanel, Readline, sortBy, sym, useOpt } from "./shared";
import type { OptimizeOut } from "./types";

export function AllocationTab({ q, onApply, applied }: { q: UseQueryResult<OptimizeOut, ApiError>; onApply: (d: OptimizeOut) => void; applied: boolean }) {
  const { cfg } = useOpt();
  const cur = currentInUniverse(cfg);
  const d = q.data;
  const isTree = d?.result.method === "hrp" || d?.result.method === "herc";
  const code = d ? METHOD_CODE[d.result.method] : METHOD_CODE[cfg.method];

  return (
    <div className="op-tab-body">
      <QPanel<OptimizeOut>
        q={q}
        title={`${code} · Ex-ante`}
        asOf={d?.universe.end}
        skeletonHeight={120}
        actions={
          d &&
          (applied ? (
            <Link to="/portfolio" className="oc-go">
              APPLIED · PORT →
            </Link>
          ) : (
            <button type="button" className="btn btn-sm btn-primary" onClick={() => onApply(d)} title="Replace the active portfolio's holdings with these weights">
              APPLY TO PORT
            </button>
          ))
        }
      >
        {(d) => <Headline d={d} current={cur} />}
      </QPanel>

      {!q.isError && (
        <>
          <QPanel<OptimizeOut>
            q={q}
            title={d ? `Allocation · ${d.universe.tickers.length} assets` : "Allocation"}
            asOf={d?.universe.end}
            flush
            skeletonHeight={260}
            provenance={[]}
            notes={[]}
            actions={d && <span className="num op-head-note">1/N RISK {fmtPct(1 / d.universe.tickers.length, 1)}</span>}
          >
            {(d) => <AllocationTable d={d} current={cur} />}
          </QPanel>

          {isTree && d?.result.extra.dendrogram && (
            <div className={d.result.extra.bisection ? "grid-3" : ""}>
              <Panel
                span={d.result.extra.bisection ? 2 : undefined}
                title={
                  <>
                    {d.result.method === "hrp" ? "HRP" : "HERC"} tree · {String(d.result.params.linkage ?? "").toUpperCase()}
                    <Note n={4} to={METHOD_DOC[d.result.method]} />
                  </>
                }
                asOf={d.universe.end}
              >
                <DendroChart d={d.result.extra.dendrogram} weights={d.result.weights} height={320} ariaLabel="Clustering tree with weights" />
              </Panel>
              {d.result.extra.bisection && (
                <Panel title={sym("Bisection · α to left")} info={INFO.bisection} asOf={d.universe.end}>
                  <Bisection steps={d.result.extra.bisection} />
                </Panel>
              )}
            </div>
          )}

          <QPanel<OptimizeOut>
            q={q}
            title={sym(d?.expected_returns.model === "historical" ? "μ · Hist mean · ±2 SE" : `μ · ${RETURNS_CODE[d?.expected_returns.model ?? "historical"]} vs hist`)}
            info={d?.expected_returns.model === "historical" ? INFO.standardError : undefined}
            asOf={d?.universe.end}
            skeletonHeight={240}
            notes={[]}
            provenance={[]}
            flush={d?.expected_returns.model !== "historical"}
          >
            {(d) => <Expected d={d} />}
          </QPanel>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ headline
function Headline({ d, current }: { d: OptimizeOut; current: Record<string, number> | null }) {
  const r = d.result;
  const n = d.universe.tickers.length;
  const turnover = current ? d.universe.tickers.reduce((a, t) => a + Math.abs((r.weights[t] ?? 0) - (current[t] ?? 0)), 0) : null;
  const rf = d.risk_free.source === "user" ? `RF ${fmtPct(d.risk_free.annual, 2)} · MANUAL` : d.risk_free.source === "market" ? `RF ${fmtPct(d.risk_free.annual, 2)} · 3M BILL` : "RF UNAVAILABLE";
  return (
    <>
      <StatGrid min={124}>
        <StatTile label="Exp return" value={r.expected_return} format={(v) => fmtPct(v, 1, { signed: true })} info={INFO.exAnteReturn} caption="ANN · EX-ANTE" />
        <StatTile label="Vol" value={r.volatility} format={(v) => fmtPct(v, 1)} info={INFO.exAnteVol} caption="ANN · EX-ANTE" />
        <StatTile label="Sharpe" value={r.sharpe} format={(v) => fmtNum(v, 2)} info={INFO.exAnteSharpe} caption={rf} />
        <StatTile label="Eff bets" value={r.effective_bets} format={(v) => fmtNum(v, 2)} info={INFO.enb} caption={`OF ${n}`} />
        <StatTile label="Div ratio" value={r.diversification_ratio} format={(v) => `${fmtNum(v, 2)}×`} info={INFO.diversificationRatio} />
        <StatTile label="Eff N" value={r.effective_n} format={(v) => fmtNum(v, 1)} info={INFO.effectiveN} caption={`OF ${n}`} />
        {r.extra.cvar_daily != null && (
          <StatTile
            label={`CVaR ${fmtPct(Number(r.params.cvar_alpha ?? 0.95), 0)} · 1D`}
            value={r.extra.cvar_daily}
            format={(v) => fmtPct(v, 2)}
            tone="loss"
            info={INFO.cvar}
            caption={r.extra.var_daily != null ? `VAR ${fmtPct(r.extra.var_daily, 2)} · ${r.extra.scenarios ?? "—"}D` : undefined}
          />
        )}
        {turnover != null && <StatTile label="Turnover" value={turnover} format={(v) => fmtPct(v, 0)} info={INFO.turnover} caption="ONE-WAY · VS CURRENT" />}
      </StatGrid>
      <Readline
        items={[
          { k: "OBS", v: `${fmtNum(d.universe.observations, 0)}D` },
          { k: "WINDOW", v: `${d.universe.start} – ${d.universe.end}` },
          { k: "Σ", v: COV_CODE[d.covariance.estimator] ?? d.covariance.estimator.toUpperCase() },
          d.covariance.shrinkage != null && { k: "δ", v: fmtNum(d.covariance.shrinkage, 3) },
          d.covariance.condition_number != null && { k: "κ", v: fmtNum(d.covariance.condition_number, 0) },
          { k: "μ", v: RETURNS_CODE[d.expected_returns.model] },
        ]}
      />
      {r.notes.length > 0 && (
        <ol className="op-result-notes">
          {r.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ol>
      )}
    </>
  );
}

// ------------------------------------------------------------------ allocation table
type Row = { ticker: string; weight: number; current: number | null; trade: number | null; rc: number; mu: number | null; vol: number; sharpe: number | null; hist: number };

function AllocationTable({ d, current }: { d: OptimizeOut; current: Record<string, number> | null }) {
  const rows = useMemo<Row[]>(() => {
    const assets = new Map(d.assets.map((a) => [a.ticker, a]));
    return d.allocation.map((a) => {
      const as = assets.get(a.ticker);
      const c = current ? (current[a.ticker] ?? 0) : null;
      return { ticker: a.ticker, weight: a.weight, current: c, trade: c == null ? null : a.weight - c, rc: a.risk_contribution_pct, mu: a.expected_return, vol: as?.volatility ?? NaN, sharpe: as?.sharpe ?? null, hist: as?.historical_mean ?? NaN };
    });
  }, [d, current]);
  const maxW = Math.max(0.01, ...rows.flatMap((r) => [Math.abs(r.weight), Math.abs(r.rc), Math.abs(r.current ?? 0)]));
  const cols: Column<Row>[] = [
    {
      key: "ticker",
      label: "Asset",
      render: (r) => (
        <Link to={`/ticker/${r.ticker}`} className="num op-tk">
          {r.ticker}
        </Link>
      ),
    },
    { key: "weight", label: "Weight", numeric: true, render: (r) => <BarCell v={r.weight} max={maxW} text={fmtPct(r.weight, 1)} />, width: "18%" },
    ...(current
      ? [
          { key: "current", label: "Current", numeric: true, format: (v: number) => fmtPct(v, 1), hideBelow: 600 } as Column<Row>,
          { key: "trade", label: "Trade", numeric: true, format: (v: number) => fmtPct(v, 1, { signed: true }) } as Column<Row>,
        ]
      : []),
    { key: "rc", label: "Risk share", numeric: true, render: (r) => <BarCell v={r.rc} max={maxW} text={fmtPct(r.rc, 1)} dim />, info: INFO.riskContribution, width: "18%" },
    { key: "mu", label: sym("μ"), numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), hideBelow: 600 },
    { key: "hist", label: sym("μ hist"), numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), hideBelow: 900 },
    { key: "vol", label: "Vol", numeric: true, format: (v) => fmtPct(v, 1), info: "vol", hideBelow: 900 },
    { key: "sharpe", label: "SR", numeric: true, format: (v) => fmtNum(v, 2), info: "sharpe", hideBelow: 900 },
  ];
  return <DataTable<Row> columns={cols} rows={rows} rowKey={(r) => r.ticker} defaultSort={{ key: "weight", dir: "desc" }} />;
}

// ------------------------------------------------------------------ bisection
function Bisection({ steps }: { steps: { left: string[]; right: string[]; alpha: number; var_left: number; var_right: number }[] }) {
  return (
    <ol className="op-bisect">
      {steps.map((s, i) => (
        <li key={i}>
          <div className="op-bisect-row num">
            <span>
              {fmtPct(s.alpha, 0)} {s.left.join(" ")}
            </span>
            <span className="op-bisect-r">
              {s.right.join(" ")} {fmtPct(1 - s.alpha, 0)}
            </span>
          </div>
          <div className="op-bisect-bar" aria-hidden style={{ "--w": `${s.alpha * 100}%` } as CSSProperties} />
          <div className="op-bisect-var num">
            σ² {fmtNum(s.var_left, 4)} · {fmtNum(s.var_right, 4)}
          </div>
        </li>
      ))}
    </ol>
  );
}

// ------------------------------------------------------------------ expected returns
type MuRow = { ticker: string; mu: number; hist: number | null; diff: number | null };

function Expected({ d }: { d: OptimizeOut }) {
  const e = d.expected_returns;
  const order = useMemo(() => sortBy(d.universe.tickers, e.mu), [d, e]);
  const se = e.standard_error;
  if (e.model === "historical" && se) {
    const rows = order.map((k) => ({ term: k, est: e.mu[k], lo: e.mu[k] - 2 * (se[k] ?? 0), hi: e.mu[k] + 2 * (se[k] ?? 0), t: se[k] ? e.mu[k] / se[k] : 0 }));
    return <CoefChart rows={rows} tick={(v) => fmtPct(v, 0)} ariaLabel="Historical mean return per asset with two standard errors" />;
  }
  const hist = new Map(d.assets.map((a) => [a.ticker, a.historical_mean]));
  const rows: MuRow[] = order.map((k) => {
    const h = hist.get(k) ?? null;
    return { ticker: k, mu: e.mu[k], hist: h, diff: h == null ? null : e.mu[k] - h };
  });
  const cols: Column<MuRow>[] = [
    { key: "ticker", label: "Asset", render: (r) => <span className="num">{r.ticker}</span> },
    { key: "mu", label: sym(`μ ${RETURNS_CODE[e.model]}`), numeric: true, format: (v) => fmtPct(v, 2, { signed: true }) },
    { key: "hist", label: sym("μ hist"), numeric: true, format: (v) => fmtPct(v, 2, { signed: true }) },
    { key: "diff", label: "Δ", numeric: true, format: (v) => fmtPct(v, 2, { signed: true }) },
  ];
  return <DataTable<MuRow> columns={cols} rows={rows} rowKey={(r) => r.ticker} />;
}
