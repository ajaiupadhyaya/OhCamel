/**
 * Row 1: four headline figures. SPY and QQQ (the S&P 500 and Nasdaq-100 ETFs) and the VIX from
 * the market overview; the 10-year par yield from FRED DGS10. Changes are 1D and signed.
 */
import { Provenance, StatGrid, StatTile } from "../../components";
import { fmtBps, fmtNum, fmtPct, fmtPctPoints } from "../../lib/format";
import { fmtStamp } from "../../design/stamp";
import type { ProvenanceRecord } from "../../lib/types";
import { lastPair, useFrontOverview, useTreasuries } from "./data";

export function Figures() {
  const ov = useFrontOverview();
  const tsy = useTreasuries();
  const row = (t: string) => ov.data?.rows.find((r) => r.ticker === t);
  const ten = tsy.data ? lastPair(tsy.data.data, "DGS10") : null;
  const tenChg = ten && ten.prev !== null ? (ten.last - ten.prev) / 100 : null;
  const prov: ProvenanceRecord[] = [...(ov.data?.provenance ?? []), ...(tsy.data?.provenance ?? [])];

  const quote = (ticker: string, label: string, name: string, invert = false) => {
    const r = row(ticker);
    const missing = ov.isError || (r && r.error) || (ov.data && !r);
    return (
      <StatTile
        size="lg"
        label={label}
        loading={ov.isLoading}
        value={missing ? null : r?.last != null ? fmtNum(r.last, 2) : null}
        delta={missing ? null : r?.ret_1d}
        deltaFormat={(d) => fmtPct(d, 2, { signed: true })}
        deltaLabel={missing ? undefined : "1D"}
        invert={invert}
        caption={missing ? "UNAVAILABLE" : `${name} · ${fmtStamp(r?.as_of ?? null)}`}
      />
    );
  };

  return (
    <div className="fp-figures">
      <StatGrid min={150}>
        {quote("SPY", "SPY", "S&P 500")}
        {quote("QQQ", "QQQ", "NASDAQ-100")}
        <StatTile
          size="lg"
          label="UST 10Y"
          loading={tsy.isLoading}
          value={ten ? fmtPctPoints(ten.last, 2) : null}
          caption={ten ? `${tenChg !== null ? fmtBps(tenChg, 0, { signed: true }) + " 1D · " : ""}DGS10 · ${fmtStamp(ten.date)}` : "UNAVAILABLE"}
        />
        {quote("^VIX", "VIX", "CBOE", true)}
      </StatGrid>
      <Provenance items={prov} className="fp-figures-prov" />
    </div>
  );
}
