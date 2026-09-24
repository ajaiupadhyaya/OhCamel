/** Company profile: valuation snapshot tiles, identity & beta detail, and the price history. */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Panel, SegmentedControl, StatGrid, StatTile, TimeSeriesChart } from "../../components";
import { Icon } from "../../components/Icon";
import { fmtCompact, fmtDate, fmtNum, fmtPct, parseDate, toIsoDate } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import type { Envelope, FramePayload } from "../../lib/types";
import { INFO } from "./info";
import { money, mult, useProfile, ZoneGauge } from "./shared";
import type { Profile } from "./types";

type PQ = ReturnType<typeof useProfile>;

interface History extends Envelope {
  ticker: string;
  n: number;
  first: string | null;
  last: string | null;
  ohlcv: FramePayload;
}

export function ProfileSnapshot({ q }: { q: PQ }) {
  return (
    <Panel<Profile>
      title="Valuation snapshot"
      subtitle="What the market pays for this business today, measured against its latest reported fundamentals."
      info={{ text: "Live price × SEC cover-page shares gives market cap; multiples divide it (or enterprise value) by trailing-twelve-month fundamentals when four consecutive quarters are available, otherwise by the latest fiscal year." }}
      query={q}
      skeletonHeight={120}
    >
      {(p) => (
        <StatGrid min={150}>
          <StatTile label="Price" value={p.price} format={(v) => fmtCurrency2(v)} info={INFO.price} caption={p.price_as_of ? fmtDate(p.price_as_of) : undefined} />
          <StatTile label="Market cap" value={money(p.market_cap)} info={INFO.market_cap} />
          <StatTile label="Enterprise value" value={money(p.enterprise_value)} info={INFO.ev} />
          <StatTile label="P/E" value={mult(p.multiples.pe)} info={INFO.pe} caption={p.multiples.pe == null ? "loss-making" : undefined} />
          <StatTile label="EV / EBITDA" value={mult(p.multiples.ev_ebitda)} info={INFO.ev_ebitda} />
          <StatTile label="EV / Sales" value={mult(p.multiples.ev_sales)} info={INFO.ev_sales} />
          <StatTile label="P / Book" value={mult(p.multiples.price_to_book)} info={INFO.pb} />
          <StatTile label="FCF yield" value={p.multiples.fcf_yield} format={(v) => fmtPct(v, 2)} tone="auto" info={INFO.fcf_yield} />
          <StatTile label="Shareholder yield" value={p.multiples.shareholder_yield} format={(v) => fmtPct(v, 2)} info={INFO.shareholder_yield} caption={p.multiples.dividend_yield != null ? `div ${fmtPct(p.multiples.dividend_yield, 2)}` : undefined} />
          <StatTile label="Beta vs SPY" value={p.beta?.beta ?? null} format={(v) => fmtNum(v, 2)} info={INFO.beta} caption={p.beta ? `Blume ${fmtNum(p.beta.blume_adjusted, 2)}` : "unavailable"} />
        </StatGrid>
      )}
    </Panel>
  );
}

const fmtCurrency2 = (v: number) => `$${fmtNum(v, 2)}`;

export function ProfileDetails({ q, ticker }: { q: PQ; ticker: string }) {
  return (
    <Panel<Profile> title="Identity & inputs" subtitle="Where the snapshot's numbers come from." query={q} skeletonHeight={300} notes={[]}>
      {(p) => (
        <div className="co-details">
          <dl className="co-dl">
            <dt>SEC CIK</dt>
            <dd className="num">
              {p.cik ? (
                <a href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${encodeURIComponent(p.cik)}&type=10-K`} target="_blank" rel="noreferrer">
                  {p.cik} <Icon name="external" size={12} />
                </a>
              ) : (
                "—"
              )}
            </dd>
            <dt>Shares outstanding</dt>
            <dd className="num">
              {fmtCompact(p.shares_outstanding)} <span className="subtle">{p.shares_outstanding_as_of ? `as of ${fmtDate(p.shares_outstanding_as_of)}` : ""}</span>
            </dd>
            <dt>Latest fiscal year</dt>
            <dd className="num">{fmtDate(p.latest_fiscal_year_end)}</dd>
            <dt>Multiples basis</dt>
            <dd>
              {p.multiples_basis === "ttm" ? "Trailing twelve months" : "Latest fiscal year"} <span className="subtle num">to {fmtDate(p.multiples_period_end)}</span>
            </dd>
            {p.beta && (
              <>
                <dt>Beta regression</dt>
                <dd className="num">
                  β {fmtNum(p.beta.beta, 2)} ± {fmtNum(p.beta.se, 2)} · R² {fmtNum(p.beta.r2, 2)} · {p.beta.n_months} months{p.beta.excess_returns ? "" : " (raw returns)"}
                </dd>
              </>
            )}
          </dl>
          {p.range_52w && (
            <div className="co-range">
              <div className="co-mini-label">
                52-week range
              </div>
              <ZoneGauge
                min={p.range_52w.low}
                max={p.range_52w.high}
                zones={[{ from: p.range_52w.low, to: p.range_52w.high, tone: "neutral", label: "" }]}
                value={p.price}
                format={(v) => fmtNum(v, 2)}
                caption={`low ${fmtDate(p.range_52w.low_date)} · high ${fmtDate(p.range_52w.high_date)}`}
              />
            </div>
          )}
          <div className="co-links">
            <Link className="btn btn-sm" to={`/ticker/${encodeURIComponent(ticker)}`}>
              <Icon name="ticker" size={14} /> Price &amp; returns
            </Link>
            <Link className="btn btn-sm" to={`/options?ticker=${encodeURIComponent(ticker)}`}>
              <Icon name="options" size={14} /> Volatility
            </Link>
          </div>
        </div>
      )}
    </Panel>
  );
}

const RANGES = ["1Y", "5Y", "Max"] as const;
type Range = (typeof RANGES)[number];

export function PriceHistory({ ticker, compact }: { ticker: string; compact?: boolean }) {
  const [range, setRange] = useState<Range>("5Y");
  const q = useApiQuery<History>(`/market/history/${encodeURIComponent(ticker)}`, undefined, { placeholderData: undefined });
  const series = useMemo(() => {
    const f = q.data?.ohlcv;
    if (!f?.index.length) return null;
    const dates = f.index as string[];
    const adj = f.data["adj_close"] as (number | null)[];
    const last = parseDate(dates[dates.length - 1])!;
    const years = range === "1Y" ? 1 : range === "5Y" ? 5 : null;
    const start = years ? toIsoDate(new Date(last.getFullYear() - years, last.getMonth(), last.getDate())) : "";
    const i0 = start ? Math.max(0, dates.findIndex((d) => d > start)) : 0;
    const y = adj.slice(i0);
    const x = dates.slice(i0);
    const first = y.find((v) => v != null) ?? null;
    const lastV = [...y].reverse().find((v) => v != null) ?? null;
    return { x, y, ret: first && lastV ? lastV / first - 1 : null };
  }, [q.data, range]);
  return (
    <Panel<History>
      title="Share price"
      subtitle={series?.ret != null ? `Adjusted close · ${range === "Max" ? "full history" : range}: ${series.ret >= 0 ? "+" : ""}${fmtPct(series.ret, 1)}` : "Adjusted close"}
      info={{ text: "Daily adjusted close as published by the price source (the panel notes say whether dividends are folded in or only splits). Open the Ticker page for volume, drawdowns and volatility." }}
      query={q}
      compact={compact}
      skeletonHeight={260}
      actions={q.isError ? undefined : <SegmentedControl size="sm" options={[...RANGES]} value={range} onChange={setRange} ariaLabel="Price range" />}
    >
      {() => series && <TimeSeriesChart series={[{ name: ticker, x: series.x, y: series.y }]} area yFormat="usd" height={260} />}
    </Panel>
  );
}
