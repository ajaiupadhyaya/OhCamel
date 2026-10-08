/**
 * Ticker (/ticker/:ticker) — one security's price tape.
 *
 * Data: GET /api/market/history/{ticker}         daily OHLCV frame (offline OK: committed bars)
 *       GET /api/market/overview?tickers={ticker} server-computed returns, 1M vol, 52W range, trend
 * The window figures, drawdown, rolling volatility, return distribution, volume figures and the
 * calendar-month table are display transforms of the adjusted closes on screen, computed in the
 * browser (./ticker/derive.ts, lib/stats.ts); each cell says so in its notes. Paper Tape: ruled
 * Cells, caps labels, mono numbers, signal red only for a loss.
 */
import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { XYChart } from "../charts/XYChart";
import { DataTable, HistogramChart, Page, Panel, SegmentedControl, StatGrid, StatTile, Toggle, useTabParam, type Column } from "../components";
import { Note } from "../design";
import { DataUnavailableError } from "../lib/api";
import { fmtCompact, fmtDate, fmtNum, fmtPct, fmtSignedPct, signClass } from "../lib/format";
import { useUniverses } from "../lib/market";
import { usePortfolio } from "../lib/portfolio";
import { useApiQuery } from "../lib/query";
import { pathStats, rollingVol, simpleReturns } from "../lib/stats";
import type { Envelope, FramePayload } from "../lib/types";
import { useOverview } from "./markets/data";
import { RANGES, drawdown, monthlyTable, sliceWindow, type MonthRow, type Range, type View } from "./ticker/derive";
import "./ticker/ticker.css";

interface History extends Envelope {
  ticker: string;
  n: number;
  first: string | null;
  last: string | null;
  ohlcv: FramePayload;
}

const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
const BROWSER = "Browser: display transform of the adjusted closes; no risk-free rate subtracted.";
const day = (d: string | null | undefined) => fmtDate(d, "short-year").toUpperCase();

export default function Ticker() {
  const { ticker: raw = "SPY" } = useParams();
  const ticker = raw.toUpperCase();
  const [range, setRange] = useTabParam<Range>("range", "1Y");
  const [logParam, setLog] = useTabParam<"1" | "0">("log", "0");
  const log = logParam === "1";
  const hist = useApiQuery<History>(`/market/history/${encodeURIComponent(ticker)}`, undefined, { placeholderData: undefined });
  const ov = useOverview(null, [ticker]);
  const universes = useUniverses();
  const { portfolio, setHoldings } = usePortfolio();

  const row = ov.data?.rows?.[0];
  const name = useMemo(() => {
    for (const u of universes.data ?? []) if (u.names?.[ticker]) return u.names[ticker];
    return row?.name && row.name !== ticker ? row.name : undefined;
  }, [universes.data, ticker, row]);

  const view = useMemo(() => (hist.data ? sliceWindow(hist.data.ohlcv, range) : null), [hist.data, range]);
  const stats = useMemo(() => (view ? pathStats(view.dates, view.adj) : null), [view]);
  const last = hist.data?.last ?? undefined;
  const inBook = portfolio.holdings.some((h) => h.ticker === ticker);
  const addToBook = () => setHoldings([...portfolio.holdings.map((h) => ({ ...h, weight: h.weight * (1 - 1 / (portfolio.holdings.length + 1)) })), { ticker, weight: 1 / (portfolio.holdings.length + 1) }]);

  return (
    <Page
      eyebrow={name ? name.toUpperCase() : undefined}
      title={<span className="num tk-symbol">{ticker}</span>}
      docTitle={ticker}
      meta={
        <>
          {row && !row.error && row.last != null && (
            <>
              <span>
                LAST <b className="num">{fmtNum(row.last, 2)}</b>
              </span>
              <span>
                1D <b className={`num ${signClass(row.ret_1d)}`}>{fmtSignedPct(row.ret_1d)}</b>
              </span>
              {row.as_of && <span>CLOSE {day(row.as_of)}</span>}
            </>
          )}
          {hist.data && (
            <span>
              {fmtNum(hist.data.n, 0)} SESSIONS · {day(hist.data.first)} – {day(hist.data.last)}
            </span>
          )}
        </>
      }
      actions={
        <div className="tk-actions">
          <Link className="btn btn-sm" to={`/company/${encodeURIComponent(ticker)}`}>
            CO
          </Link>
          <Link className="btn btn-sm" to={`/options?ticker=${encodeURIComponent(ticker)}`}>
            VOL
          </Link>
          <button type="button" className="btn btn-sm" disabled={inBook} onClick={addToBook}>
            {inBook ? "IN BOOK" : "ADD TO BOOK"}
          </button>
        </div>
      }
    >
      <Panel<History>
        title={`PRICE · ADJ CLOSE · ${range}`}
        query={hist}
        skeletonHeight={420}
        asOf={last}
        actions={
          <div className="tk-ctl">
            <SegmentedControl size="sm" options={[...RANGES]} value={range} onChange={setRange} ariaLabel="Range" />
            <Toggle label="LOG" checked={log} onChange={(v) => setLog(v ? "1" : "0")} />
          </div>
        }
      >
        {() =>
          view && (
            <div className="tk-price">
              <XYChart time x={view.dates} series={[{ name: ticker, y: view.adj }]} logY={log} yFormat="num" digits={2} height={340} ariaLabel={`${ticker} adjusted close, ${range}`} />
              <XYChart time x={view.dates} series={[{ name: "VOL M", y: view.volume.map((v) => (v == null ? null : v / 1e6)), mode: "bars", tone: "ink3", label: true }]} yFormat="num" digits={1} height={96} zero ariaLabel={`${ticker} daily volume in millions of shares, ${range}`} />
            </div>
          )
        }
      </Panel>

      {!hist.isError && (
        <>
          <div className="grid-2">
            <Panel
              title={
                <>
                  RETURNS · SERVER
                  <Note n={1} to="returns" />
                </>
              }
              query={ov}
              error={ov.error ?? (row?.error ? new DataUnavailableError(row.error, "/api/market/overview") : undefined)}
              skeletonHeight={100}
              asOf={row?.as_of ?? undefined}
            >
              {() =>
                row && (
                  <StatGrid min={120}>
                    <StatTile size="sm" label="1M" value={row.ret_1m} format={(v) => fmtSignedPct(v, 1)} tone="auto" />
                    <StatTile size="sm" label="YTD" value={row.ret_ytd} format={(v) => fmtSignedPct(v, 1)} tone="auto" info="ytd" />
                    <StatTile size="sm" label="1Y" value={row.ret_1y} format={(v) => fmtSignedPct(v, 1)} tone="auto" />
                    <StatTile size="sm" label="3Y ANN" value={row.ret_3y_ann} format={(v) => fmtSignedPct(v, 1)} tone="auto" info="cagr" />
                    <StatTile size="sm" label="VOL 1M" value={row.vol_1m_ann} format={(v) => fmtPct(v, 1)} info="realized_vol" caption="ANN" />
                    <StatTile size="sm" label="FROM 52W HIGH" value={row.dist_52w_high} format={(v) => fmtSignedPct(v, 1)} tone="auto" caption={row.high_52w != null ? `HIGH ${fmtNum(row.high_52w, 2)}` : undefined} />
                    <StatTile size="sm" label="VS 200D SMA" value={row.sma200_pos} format={(v) => fmtSignedPct(v, 1)} tone="auto" info={{ title: "Trend vs 200-day average", text: "Price relative to its mean over the last 200 sessions.", formula: "P_t / \\overline{P}_{200} - 1" }} />
                  </StatGrid>
                )
              }
            </Panel>

            <Panel title={`WINDOW · ${range}${view ? ` · ${day(view.dates[0])} – ${day(view.dates[view.dates.length - 1])}` : ""}`} loading={hist.isLoading} error={hist.error} notes={BROWSER} provenance={[]} skeletonHeight={100} asOf={last}>
              {stats && (
                <StatGrid min={120}>
                  <StatTile size="sm" label="TOTAL" value={stats.total} format={(v) => fmtSignedPct(v, 1)} tone="auto" />
                  <StatTile size="sm" label="CAGR" value={stats.years >= 0.95 ? stats.cagr : null} format={(v) => fmtSignedPct(v, 1)} tone="auto" info="cagr" caption={stats.years < 0.95 ? "< 1Y WINDOW" : undefined} />
                  <StatTile size="sm" label="VOL" value={stats.vol} format={(v) => fmtPct(v, 1)} info="vol" caption="ANN" />
                  <StatTile size="sm" label="RET / VOL" value={stats.returnToVol} format={(v) => fmtNum(v, 2)} info={{ title: "Return-to-volatility", text: "Mean daily return over its standard deviation, annualized; no risk-free rate.", formula: "\\frac{\\bar r}{\\sigma_r}\\sqrt{252}" }} />
                  <StatTile size="sm" label="MAX DD" value={stats.maxDrawdown} format={(v) => fmtPct(v, 1)} tone={stats.maxDrawdown < 0 ? "loss" : "neutral"} info="max_drawdown" />
                  <StatTile size="sm" label="WORST DAY" value={stats.worst} format={(v) => fmtSignedPct(v, 2)} tone={stats.worst < 0 ? "loss" : "neutral"} caption={`BEST ${fmtSignedPct(stats.best, 2)}`} />
                </StatGrid>
              )}
            </Panel>
          </div>

          <div className="grid-2">
            <Panel title={`DRAWDOWN · ${range}`} loading={hist.isLoading} error={hist.error} notes={BROWSER} provenance={[]} skeletonHeight={240} asOf={last}>
              {view && stats && <DrawdownChart view={view} maxDd={stats.maxDrawdown} />}
            </Panel>
            <Panel
              title={
                <>
                  REALIZED VOL · 21D 63D · ANN
                  <Note n={2} to="realized-vol" />
                </>
              }
              loading={hist.isLoading}
              error={hist.error}
              notes="Browser: rolling stdev of daily log returns × √252."
              provenance={[]}
              skeletonHeight={240}
              asOf={last}
            >
              {view && hist.data && <VolChart dates={hist.data.ohlcv.index as string[]} adj={hist.data.ohlcv.data["adj_close"] as (number | null)[]} from={view.dates[0]} />}
            </Panel>
            <Panel title={`DAILY RETURNS · ${range}`} loading={hist.isLoading} error={hist.error} notes={BROWSER} provenance={[]} skeletonHeight={240} asOf={last}>
              {view && stats && <HistogramChart values={simpleReturns(view.adj)} format="pct" digits={2} vlines={[{ x: stats.q05, label: `Q05 ${fmtPct(stats.q05, 2)}` }]} height={240} />}
            </Panel>
            <Panel title={`VOLUME · ${range}`} loading={hist.isLoading} error={hist.error} notes={[]} provenance={[]} skeletonHeight={240} asOf={last}>
              {view && <VolumeFigures view={view} />}
            </Panel>
          </div>

          <Panel<History> title="MONTHLY RETURNS · FULL HISTORY" query={hist} flush notes={BROWSER} skeletonHeight={260} asOf={last}>
            {(h) => <MonthlyTable history={h} />}
          </Panel>
        </>
      )}
    </Page>
  );
}

function DrawdownChart({ view, maxDd }: { view: View; maxDd: number }) {
  const dd = useMemo(() => drawdown(view.adj), [view]);
  return (
    <XYChart
      time
      x={view.dates}
      series={[{ name: "DD", y: dd }]}
      yFormat="pct"
      digits={1}
      height={240}
      zero
      hlines={maxDd < 0 ? [{ at: maxDd, label: `MAX ${fmtPct(maxDd, 1)}`, tone: "signal", dash: "dot" }] : []}
      ariaLabel="Drawdown from running peak"
    />
  );
}

function VolChart({ dates, adj, from }: { dates: string[]; adj: (number | null)[]; from: string }) {
  const d = useMemo(() => {
    const i0 = Math.max(0, dates.findIndex((x) => x >= from));
    return { x: dates.slice(i0), v21: rollingVol(adj, 21).slice(i0), v63: rollingVol(adj, 63).slice(i0) };
  }, [dates, adj, from]);
  return (
    <XYChart
      time
      x={d.x}
      series={[
        { name: "21D", y: d.v21 },
        { name: "63D", y: d.v63, tone: "ink2", dash: "dash" },
      ]}
      yFormat="pct"
      digits={1}
      height={240}
      zero
      ariaLabel="Rolling realized volatility, 21 and 63 sessions"
    />
  );
}

function VolumeFigures({ view }: { view: View }) {
  const vols = view.volume.filter((v): v is number => v != null && Number.isFinite(v) && v > 0);
  if (!vols.length) return <div className="tk-none">NO VOLUME IN THE SOURCE</div>;
  const lastV = vols[vols.length - 1];
  const tail = vols.slice(-21);
  const avg21 = tail.reduce((a, b) => a + b, 0) / tail.length;
  const avgAll = vols.reduce((a, b) => a + b, 0) / vols.length;
  return (
    <StatGrid min={120}>
      <StatTile size="sm" label="LAST" value={fmtCompact(lastV, 2)} caption="SHARES" />
      <StatTile size="sm" label="AVG 21D" value={fmtCompact(avg21, 2)} caption={`LAST / AVG ${fmtNum(lastV / avg21, 2)}×`} />
      <StatTile size="sm" label="AVG WINDOW" value={fmtCompact(avgAll, 2)} caption={`${fmtNum(vols.length, 0)} SESSIONS`} />
    </StatGrid>
  );
}

function MonthlyTable({ history }: { history: History }) {
  const rows = useMemo(() => monthlyTable(history.ohlcv.index as string[], history.ohlcv.data["adj_close"] as (number | null)[]), [history]);
  const cols: Column<MonthRow>[] = [
    { key: "year", label: "YEAR", render: (r) => <span className="num">{r.year}</span>, sortable: false },
    ...MONTHS.map((m, i) => ({ key: m, label: m, numeric: true, sortable: false, value: (r: MonthRow) => r.months[i], format: (v: number | null) => fmtPct(v, 1), color: "sign" as const })),
    {
      key: "year_ret",
      label: "YR",
      numeric: true,
      sortable: false,
      value: (r) => r.year_ret,
      render: (r) => (
        <span className={`tk-yr ${signClass(r.year_ret)}`}>
          {fmtPct(r.year_ret, 1)}
          {r.n < 12 && <span className="tk-yr-n">{r.n}M</span>}
        </span>
      ),
    },
  ];
  return <DataTable columns={cols} rows={rows} rowKey={(r) => r.year} scrollLabel="Monthly returns by year" />;
}
