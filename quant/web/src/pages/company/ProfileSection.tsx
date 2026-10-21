/** Company profile: the valuation figures, identity and inputs, and the share-price cell. */
import { useMemo } from "react";
import { XYChart } from "../../charts/XYChart";
import { Panel, SegmentedControl, StatGrid, StatTile, useTabParam } from "../../components";
import { Note } from "../../design";
import { fmtCompact, fmtDate, fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import type { Envelope, FramePayload } from "../../lib/types";
import { INFO } from "./info";
import { KV, ZoneTrack, money, mult, useProfile } from "./shared";
import type { Profile } from "./types";

type PQ = ReturnType<typeof useProfile>;

interface History extends Envelope {
  ticker: string;
  n: number;
  first: string | null;
  last: string | null;
  ohlcv: FramePayload;
}

const day = (d: string | null | undefined) => fmtDate(d, "short-year").toUpperCase();

export function ProfileSnapshot({ q }: { q: PQ }) {
  const p = q.data;
  return (
    <Panel<Profile>
      title={
        <>
          VALUATION · {p?.multiples_basis === "fy" ? "FY" : "TTM"}
          {p ? ` TO ${day(p.multiples_period_end)}` : ""}
          <Note n={1} to="ratios" />
        </>
      }
      query={q}
      skeletonHeight={120}
      asOf={p?.price_as_of ?? undefined}
    >
      {(p) => (
        <StatGrid min={130}>
          <StatTile size="sm" label="PRICE" value={p.price} format={(v) => `$${fmtNum(v, 2)}`} info={INFO.price} />
          <StatTile size="sm" label="MKT CAP" value={money(p.market_cap)} info={INFO.market_cap} />
          <StatTile size="sm" label="EV" value={money(p.enterprise_value)} info={INFO.ev} />
          <StatTile size="sm" label="P / E" value={mult(p.multiples.pe)} info={INFO.pe} caption={p.multiples.pe == null ? "NEG EARNINGS" : undefined} />
          <StatTile size="sm" label="EV / EBITDA" value={mult(p.multiples.ev_ebitda)} info={INFO.ev_ebitda} />
          <StatTile size="sm" label="EV / SALES" value={mult(p.multiples.ev_sales)} info={INFO.ev_sales} />
          <StatTile size="sm" label="P / B" value={mult(p.multiples.price_to_book)} info={INFO.pb} />
          <StatTile size="sm" label="FCF YIELD" value={p.multiples.fcf_yield} format={(v) => fmtPct(v, 2)} tone={p.multiples.fcf_yield != null && p.multiples.fcf_yield < 0 ? "loss" : "neutral"} info={INFO.fcf_yield} />
          <StatTile size="sm" label="SH YIELD" value={p.multiples.shareholder_yield} format={(v) => fmtPct(v, 2)} info={INFO.shareholder_yield} caption={p.multiples.dividend_yield != null ? `DIV ${fmtPct(p.multiples.dividend_yield, 2)}` : undefined} />
          <StatTile size="sm" label="BETA · SPY" value={p.beta?.beta ?? null} format={(v) => fmtNum(v, 2)} info={INFO.beta} caption={p.beta ? `BLUME ${fmtNum(p.beta.blume_adjusted, 2)}` : "UNAVAILABLE"} />
        </StatGrid>
      )}
    </Panel>
  );
}

export function ProfileDetails({ q }: { q: PQ }) {
  return (
    <Panel<Profile> title="IDENTITY · INPUTS" query={q} skeletonHeight={300} notes={[]} asOf={q.data?.shares_outstanding_as_of ?? undefined}>
      {(p) => (
        <div className="co-details">
          <KV
            rows={[
              {
                k: "SEC CIK",
                v: p.cik ? (
                  <a href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${encodeURIComponent(p.cik)}&type=10-K`} target="_blank" rel="noreferrer">
                    {p.cik}
                  </a>
                ) : (
                  "—"
                ),
              },
              { k: "SHARES OUT", v: `${fmtCompact(p.shares_outstanding)}${p.shares_outstanding_as_of ? ` · ${day(p.shares_outstanding_as_of)}` : ""}` },
              { k: "LATEST FY", v: day(p.latest_fiscal_year_end) },
              { k: "MULTIPLES BASIS", v: `${p.multiples_basis === "ttm" ? "TTM" : "FY"} · ${day(p.multiples_period_end)}` },
              ...(p.beta
                ? [
                    { k: "β ± SE", v: `${fmtNum(p.beta.beta, 2)} ± ${fmtNum(p.beta.se, 2)}`, info: INFO.beta },
                    { k: "R² · N", v: `${fmtNum(p.beta.r2, 2)} · ${p.beta.n_months}M${p.beta.excess_returns ? "" : " RAW"}` },
                  ]
                : []),
            ]}
          />
          {p.range_52w && (
            <div className="co-range">
              <div className="co-ctl-k">
                52W RANGE · {day(p.range_52w.low_date)} – {day(p.range_52w.high_date)}
              </div>
              <ZoneTrack min={p.range_52w.low} max={p.range_52w.high} value={p.price} format={(v) => fmtNum(v, 2)} />
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

const RANGES = ["1Y", "5Y", "MAX"] as const;
type Range = (typeof RANGES)[number];

export function PriceHistory({ ticker }: { ticker: string }) {
  const [range, setRange] = useTabParam<Range>("px", "5Y");
  const q = useApiQuery<History>(`/market/history/${encodeURIComponent(ticker)}`, undefined, { placeholderData: undefined });
  const series = useMemo(() => {
    const f = q.data?.ohlcv;
    if (!f?.index.length) return null;
    const dates = f.index as string[];
    const adj = f.data["adj_close"] as (number | null)[];
    const lastD = dates[dates.length - 1];
    const years = range === "1Y" ? 1 : range === "5Y" ? 5 : null;
    const start = years ? `${Number(lastD.slice(0, 4)) - years}${lastD.slice(4)}` : "";
    const i0 = start ? Math.max(0, dates.findIndex((d) => d > start)) : 0;
    const y = adj.slice(i0);
    const first = y.find((v) => v != null) ?? null;
    const lastV = [...y].reverse().find((v) => v != null) ?? null;
    return { x: dates.slice(i0), y, ret: first && lastV ? lastV / first - 1 : null };
  }, [q.data, range]);
  return (
    <Panel<History>
      title={
        <>
          PRICE · ADJ CLOSE · {range}
          {series?.ret != null && <span className={`co-title-fig num ${series.ret < 0 ? "loss" : ""}`}>{fmtSignedPct(series.ret, 1)}</span>}
        </>
      }
      query={q}
      skeletonHeight={260}
      asOf={q.data?.last ?? undefined}
      actions={q.isError ? undefined : <SegmentedControl size="sm" options={[...RANGES]} value={range} onChange={setRange} ariaLabel="Price range" />}
    >
      {() => series && <XYChart time x={series.x} series={[{ name: ticker, y: series.y }]} yFormat="usd" digits={2} height={260} ariaLabel={`${ticker} adjusted close, ${range}`} />}
    </Panel>
  );
}
