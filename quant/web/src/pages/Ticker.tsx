/**
 * Ticker (/ticker/:ticker) — price history with volume, headline statistics, drawdowns,
 * realized volatility, return distribution and a monthly-returns calendar.
 *
 * Data: GET /api/market/history/{ticker} (daily OHLCV frame) and
 *       GET /api/market/overview?tickers={ticker} (server-computed return/vol/range metrics).
 * Window statistics, drawdowns, rolling vol and monthly returns are simple transforms of the
 * displayed adjusted closes, computed in the browser (stated in each panel's notes).
 */
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { Data } from "plotly.js";
import { Chart, HeatmapChart, HistogramChart, Page, Panel, SegmentedControl, StatGrid, StatTile, TimeSeriesChart, Toggle, withAlpha } from "../components";
import type { Info } from "../lib/glossary";
import { fmtCompact, fmtDate, fmtNum, fmtPct, fmtSignedPct, parseDate, signClass, toIsoDate } from "../lib/format";
import { useUniverses } from "../lib/market";
import { DataUnavailableError } from "../lib/api";
import { usePortfolio } from "../lib/portfolio";
import { useApiQuery } from "../lib/query";
import { monthlyReturns, pathStats, rollingVol, simpleReturns } from "../lib/stats";
import type { Tokens } from "../lib/theme";
import type { Envelope, FramePayload } from "../lib/types";
import { useOverview } from "./markets/data";
import { Icon } from "../components/Icon";
import "./ticker/ticker.css";

interface History extends Envelope {
  ticker: string;
  n: number;
  first: string | null;
  last: string | null;
  ohlcv: FramePayload;
}

const RANGES = ["1M", "3M", "6M", "YTD", "1Y", "5Y", "Max"] as const;
type Range = (typeof RANGES)[number];

function rangeStart(r: Range, last: string): string | null {
  const d = parseDate(last)!;
  const back = (m: number) => toIsoDate(new Date(d.getFullYear(), d.getMonth() - m, d.getDate()));
  switch (r) {
    case "1M": return back(1);
    case "3M": return back(3);
    case "6M": return back(6);
    case "YTD": return `${d.getFullYear() - 1}-12-31`;
    case "1Y": return back(12);
    case "5Y": return back(60);
    default: return null;
  }
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const CLIENT_NOTE = "Computed in your browser from the adjusted closes shown above (no risk-free rate subtracted).";

export default function Ticker() {
  const { ticker: raw = "SPY" } = useParams();
  const ticker = raw.toUpperCase();
  const [range, setRange] = useState<Range>("1Y");
  const [mode, setMode] = useState<"line" | "candles">("line");
  const [log, setLog] = useState(false);
  const hist = useApiQuery<History>(`/market/history/${encodeURIComponent(ticker)}`, undefined, { placeholderData: undefined });
  const ov = useOverview(null, [ticker]);
  const universes = useUniverses();
  const { portfolio, setHoldings } = usePortfolio();

  const row = ov.data?.rows?.[0];
  const name = useMemo(() => {
    for (const u of universes.data ?? []) if (u.names?.[ticker]) return u.names[ticker];
    return row?.name && row.name !== ticker ? row.name : undefined;
  }, [universes.data, ticker, row]);

  // ---- slice the window once; everything below uses it
  const view = useMemo(() => {
    const f = hist.data?.ohlcv;
    if (!f || !f.index.length) return null;
    const dates = f.index as string[];
    const start = rangeStart(range, dates[dates.length - 1]);
    const i0 = start ? Math.max(0, dates.findIndex((d) => d > start)) : 0;
    const sl = <T,>(a: T[] | undefined) => (a ?? []).slice(i0);
    const col = (c: string) => sl(f.data[c] as (number | null)[]);
    return { dates: sl(dates), open: col("open"), high: col("high"), low: col("low"), close: col("close"), adj: col("adj_close"), volume: col("volume") };
  }, [hist.data, range]);

  const stats = useMemo(() => (view ? pathStats(view.dates, view.adj) : null), [view]);
  const inPortfolio = portfolio.holdings.some((h) => h.ticker === ticker);

  return (
    <Page
      eyebrow={<Link to="/" className="subtle">← Markets</Link>}
      title={
        <span className="tk-title">
          <span className="num tk-symbol">{ticker}</span>
          {name && <span className="tk-name">{name}</span>}
        </span>
      }
      docTitle={ticker}
      subtitle={
        row && !row.error ? (
          <span className="tk-quote">
            <span className="num tk-last">{fmtNum(row.last, 2)}</span>
            <span className={`num tk-chg ${signClass(row.ret_1d)}`}>{fmtSignedPct(row.ret_1d)}</span>
            <span className="subtle small">close · {fmtDate(row.as_of)}</span>
          </span>
        ) : undefined
      }
      actions={
        <>
          <Link className="btn" to={`/company/${ticker}`}>
            <Icon name="company" size={15} /> Fundamentals
          </Link>
          <Link className="btn" to={`/options?ticker=${encodeURIComponent(ticker)}`}>
            <Icon name="options" size={15} /> Options
          </Link>
          <button className="btn btn-primary" disabled={inPortfolio} onClick={() => setHoldings([...portfolio.holdings.map((h) => ({ ...h, weight: h.weight * (1 - 1 / (portfolio.holdings.length + 1)) })), { ticker, weight: 1 / (portfolio.holdings.length + 1) }])}>
            <Icon name={inPortfolio ? "check" : "plus"} size={15} /> {inPortfolio ? "In portfolio" : "Add to portfolio"}
          </button>
        </>
      }
    >
      <Panel<History>
        title="Price"
        subtitle={mode === "line" ? "Adjusted close with daily volume (the note below says how the source adjusts it)" : "Daily open-high-low-close (unadjusted) with volume"}
        query={hist}
        skeletonHeight={460}
        actions={
          <>
            <SegmentedControl size="sm" options={[{ value: "line", label: <Icon name="line" size={14} title="Line" /> }, { value: "candles", label: <Icon name="candles" size={14} title="Candles" /> }]} value={mode} onChange={setMode} ariaLabel="Chart type" />
            <SegmentedControl size="sm" options={[...RANGES]} value={range} onChange={setRange} ariaLabel="Range" />
            <Toggle label="Log" checked={log} onChange={setLog} />
          </>
        }
      >
        {() => (view ? <PriceChart view={view} mode={mode} log={log} /> : null)}
      </Panel>

      {!hist.isError && (
      <>
      <Panel title="At a glance" query={ov} error={ov.error ?? (row?.error ? new DataUnavailableError(row.error, "/api/market/overview") : undefined)} skeletonHeight={100} info={{ text: "Server-computed from the full daily history: calendar look-back returns on adjusted closes, 1-month close-to-close volatility, 52-week range and trend vs moving averages." }}>
        {() =>
          row && (
            <StatGrid min={140}>
              <StatTile label="1 month" value={row.ret_1m} format={(v) => fmtSignedPct(v, 1)} tone="auto" />
              <StatTile label="YTD" value={row.ret_ytd} format={(v) => fmtSignedPct(v, 1)} tone="auto" info="ytd" />
              <StatTile label="1 year" value={row.ret_1y} format={(v) => fmtSignedPct(v, 1)} tone="auto" />
              <StatTile label="3Y annualized" value={row.ret_3y_ann} format={(v) => fmtSignedPct(v, 1)} tone="auto" info="cagr" />
              <StatTile label="Vol (1M)" value={row.vol_1m_ann} format={(v) => fmtPct(v, 1)} info="realized_vol" />
              <StatTile label="From 52W high" value={row.dist_52w_high} format={(v) => fmtSignedPct(v, 1)} tone="auto" caption={row.high_52w != null ? `high ${fmtNum(row.high_52w)}` : undefined} />
              <StatTile label="vs 200-day avg" value={row.sma200_pos} format={(v) => fmtSignedPct(v, 1)} tone="auto" info={{ title: "Trend vs 200-day average", text: "How far the price is above (+) or below (−) its average over the last 200 sessions — a common long-term trend gauge.", formula: "P_t / \\overline{P}_{200} - 1" }} />
            </StatGrid>
          )
        }
      </Panel>

      <Panel title={`Window: ${range}`} subtitle={view ? `${fmtDate(view.dates[0])} – ${fmtDate(view.dates[view.dates.length - 1])}` : undefined} loading={hist.isLoading} error={hist.error} notes={CLIENT_NOTE} skeletonHeight={100}>
        {stats && (
          <StatGrid min={140}>
            <StatTile label="Total return" value={stats.total} format={(v) => fmtSignedPct(v, 1)} tone="auto" />
            <StatTile label="CAGR" value={stats.years >= 0.95 ? stats.cagr : null} format={(v) => fmtSignedPct(v, 1)} tone="auto" info="cagr" caption={stats.years < 0.95 ? "needs ≥ 1 year" : undefined} />
            <StatTile label="Volatility" value={stats.vol} format={(v) => fmtPct(v, 1)} info="vol" />
            <StatTile label="Return / vol" value={stats.returnToVol} format={(v) => fmtNum(v, 2)} info={{ title: "Return-to-volatility", text: "Average daily return divided by its standard deviation, annualized. The Sharpe ratio without subtracting a risk-free rate.", formula: "\\frac{\\bar r}{\\sigma_r}\\sqrt{252}" } as Info} />
            <StatTile label="Max drawdown" value={stats.maxDrawdown} format={(v) => fmtPct(v, 1)} tone="loss" info="max_drawdown" />
            <StatTile label="Worst day" value={stats.worst} format={(v) => fmtSignedPct(v, 2)} tone="loss" caption={`best ${fmtSignedPct(stats.best, 2)}`} />
          </StatGrid>
        )}
      </Panel>

      <div className="grid-2">
        <Panel title="Drawdown" info="max_drawdown" loading={hist.isLoading} error={hist.error} notes={CLIENT_NOTE} skeletonHeight={240}>
          {view && <DrawdownChart view={view} />}
        </Panel>
        <Panel title="Realized volatility" info="realized_vol" loading={hist.isLoading} error={hist.error} notes="Rolling close-to-close volatility of daily log returns, annualized with √252. Computed in your browser." skeletonHeight={240}>
          {view && <VolChart dates={hist.data!.ohlcv.index as string[]} adj={hist.data!.ohlcv.data["adj_close"] as (number | null)[]} from={view.dates[0]} />}
        </Panel>
        <Panel title="Daily return distribution" loading={hist.isLoading} error={hist.error} info={{ text: "How often each size of daily move happened in the window. The dotted line marks the 5th percentile — the loss exceeded on roughly 1 day in 20 (historical 95% VaR)." }} skeletonHeight={260}>
          {view && stats && <HistogramChart values={simpleReturns(view.adj)} format="pct" digits={2} vlines={[{ x: stats.q05, label: `5% ${fmtPct(stats.q05, 2)}` }]} height={260} />}
        </Panel>
        <Panel title="Monthly returns" loading={hist.isLoading} error={hist.error} info={{ text: "Calendar-month returns of the adjusted close, full history. Read across a row for a year, down a column for seasonality." }} skeletonHeight={260}>
          {hist.data && <MonthlyHeatmap history={hist.data} />}
        </Panel>
      </div>

      </>
      )}

      {hist.data && (
        <p className="subtle small">
          {hist.data.n.toLocaleString()} sessions from {fmtDate(hist.data.first)} to {fmtDate(hist.data.last)}. Volume is shares traded per session ({fmtCompact(view?.volume.at(-1) ?? null)} on the last day).
        </p>
      )}
    </Page>
  );
}

type View = { dates: string[]; open: (number | null)[]; high: (number | null)[]; low: (number | null)[]; close: (number | null)[]; adj: (number | null)[]; volume: (number | null)[] };

function PriceChart({ view, mode, log }: { view: View; mode: "line" | "candles"; log: boolean }) {
  // Traces as a function of theme tokens, so colours follow the light/dark toggle.
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const price =
        mode === "candles"
          ? { type: "candlestick", x: view.dates, open: view.open, high: view.high, low: view.low, close: view.close, name: "OHLC", increasing: { line: { color: t.gain, width: 1 }, fillcolor: t.gain }, decreasing: { line: { color: t.loss, width: 1 }, fillcolor: t.loss } }
          : { type: "scatter", mode: "lines", x: view.dates, y: view.adj, name: "Adj. close", line: { width: 1.8, color: t.categorical[0] }, fill: "tozeroy", fillgradient: { type: "vertical", colorscale: [[0, withAlpha(t.categorical[0], 0)], [1, withAlpha(t.categorical[0], 0.16)]] }, hovertemplate: "<b>%{y:,.2f}</b><extra></extra>" };
      const colors = view.close.map((c, i) => (i && c != null && view.close[i - 1] != null && c < (view.close[i - 1] as number) ? t.loss : t.gain));
      const vol = { type: "bar", x: view.dates, y: view.volume, name: "Volume", yaxis: "y2", marker: { color: colors, opacity: 0.4 }, hovertemplate: "vol %{y:,.3s}<extra></extra>" };
      return [price, vol] as Data[];
    },
    [view, mode],
  );
  const layout = useMemo(() => {
    const ys = (mode === "candles" ? [...view.low, ...view.high] : view.adj).filter((v): v is number => v != null && v > 0);
    const lo = Math.min(...ys);
    const hi = Math.max(...ys);
    const pad = (hi - lo) * 0.06;
    return {
      hovermode: "x unified",
      showlegend: false,
      margin: { l: 8, r: 8, t: 8, b: 28 },
      xaxis: { type: "date", rangeslider: { visible: false }, hoverformat: "%a %d %b %Y", showline: false },
      yaxis: { domain: [0.24, 1], side: "right", type: log ? "log" : "linear", tickformat: hi >= 100 ? ",.0f" : ",.2f", range: log ? undefined : [lo - pad, hi + pad] },
      yaxis2: { domain: [0, 0.17], side: "right", showgrid: false, showticklabels: false, zeroline: false },
      annotations: [{ text: "Volume", xref: "paper", yref: "paper", x: 0, y: 0.17, xanchor: "left", yanchor: "top", showarrow: false, font: { size: 10, color: "var(--text-3)" } }],
    } as any;
  }, [log, mode, view]);
  return <Chart data={data} layout={layout} height={440} />;
}

function DrawdownChart({ view }: { view: View }) {
  const dd = useMemo(() => {
    let peak = -Infinity;
    return view.adj.map((v) => (v == null ? null : ((peak = Math.max(peak, v)), v / peak - 1)));
  }, [view]);
  return <TimeSeriesChart series={[{ name: "Drawdown", x: view.dates, y: dd, fill: true, color: "var(--loss)" }]} yFormat="pct" digits={1} height={240} baseline={0} layout={{ yaxis: { rangemode: "tozero" } } as any} />;
}

function VolChart({ dates, adj, from }: { dates: string[]; adj: (number | null)[]; from: string }) {
  const series = useMemo(() => {
    const v21 = rollingVol(adj, 21);
    const v63 = rollingVol(adj, 63);
    const i0 = Math.max(0, dates.findIndex((d) => d >= from));
    return [
      { name: "21-day", x: dates.slice(i0), y: v21.slice(i0) },
      { name: "63-day", x: dates.slice(i0), y: v63.slice(i0) },
    ];
  }, [dates, adj, from]);
  return <TimeSeriesChart series={series} yFormat="pct" digits={1} height={240} />;
}

function MonthlyHeatmap({ history }: { history: History }) {
  const m = useMemo(() => monthlyReturns(history.ohlcv.index as string[], history.ohlcv.data["adj_close"] as (number | null)[]), [history]);
  const years = m.years;
  return <HeatmapChart x={MONTHS} y={years.map(String)} z={m.z} format="pct" digits={1} showValues diverging height={Math.max(220, years.length * 28 + 40)} colorbar={false} />;
}
