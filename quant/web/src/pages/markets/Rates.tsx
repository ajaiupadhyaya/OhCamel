/**
 * RATES: the Treasury par curve now vs 1M and 1Y ago (GET /api/macro/curve?compare=1M,1Y) beside
 * the 2Y, the 10Y and 2s10s (GET /api/macro/series?ids=DGS2,DGS10). Each cell fails alone.
 */
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Panel, StatGrid, StatTile } from "../../components";
import { CurveChart, type CurveLine } from "../../charts/CurveChart";
import { fmtStamp } from "../../design/stamp";
import { EM_DASH, fmtBps, fmtPctPoints } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import { useCurve } from "../macro/CurveTab";
import { INFO } from "../macro/info";
import type { CurveOut, FredExplorer } from "../macro/types";

export function curveLines(d: CurveOut): CurveLine[] {
  const out: CurveLine[] = [{ key: "now", label: "NOW", tenors: d.par.tenors, yields: d.par.yields }];
  for (const c of d.compare) if (!c.error && c.par) out.push({ key: c.label, label: c.label, tenors: c.par.tenors, yields: c.par.yields });
  return out;
}

export function Rates() {
  const curve = useCurve(undefined, ["1M", "1Y"]);
  const tsy = useApiQuery<FredExplorer>("/macro/series", { ids: "DGS2,DGS10", start: "2024-01-01" });
  return (
    <div className="grid-3">
      <Panel<CurveOut>
        title="CURVE · UST PAR"
        query={curve}
        span={2}
        compact
        skeletonHeight={260}
        notes={[]}
        asOf={curve.data?.date}
        actions={
          <Link to="/macro?tab=curve" className="oc-go">
            RATES →
          </Link>
        }
      >
        {(d) => <CurveChart lines={curveLines(d)} height={260} ariaLabel="Treasury par curve now, 1 month and 1 year ago" />}
      </Panel>
      <Panel<FredExplorer> title="UST 2Y · 10Y" query={tsy} compact skeletonHeight={260} notes={[]} asOf={tsy.data ? lastDate(tsy.data) : undefined}>
        {(d) => <Treasuries d={d} />}
      </Panel>
    </div>
  );
}

function lastDate(d: FredExplorer): string | undefined {
  return d.data.index.length ? String(d.data.index[d.data.index.length - 1]) : undefined;
}

/** 2s10s in bp from the last date on which both legs printed. */
export function spread2s10s(d: FredExplorer): { bp: number; date: string } | null {
  const a = d.data.data.DGS2 ?? [];
  const b = d.data.data.DGS10 ?? [];
  for (let i = d.data.index.length - 1; i >= 0; i--) if (a[i] != null && b[i] != null) return { bp: 100 * ((b[i] as number) - (a[i] as number)), date: String(d.data.index[i]) };
  return null;
}

function Treasuries({ d }: { d: FredExplorer }) {
  const s2 = d.summary.DGS2;
  const s10 = d.summary.DGS10;
  const spread = useMemo(() => spread2s10s(d), [d]);
  // FRED changes are in percent points: x 100 = bp; fmtBps takes a decimal.
  const bp = (pp: number | null | undefined) => (pp == null ? EM_DASH : fmtBps(pp / 100, 0, { signed: true }).toUpperCase());
  const changes = (s: typeof s2) => (s ? `${bp(s.change["1M"])} 1M · ${bp(s.change["1Y"])} 1Y` : undefined);
  const inverted = spread !== null && spread.bp < 0;
  return (
    <StatGrid min={140}>
      <StatTile size="sm" label="2Y" value={fmtPctPoints(s2?.latest)} caption={changes(s2)} />
      <StatTile size="sm" label="10Y" value={fmtPctPoints(s10?.latest)} caption={changes(s10)} />
      <StatTile
        label="2S10S"
        info={INFO.s2s10}
        value={spread ? fmtBps(spread.bp / 1e4, 0, { signed: true }).toUpperCase() : null}
        tone={inverted ? "loss" : "neutral"}
        caption={spread ? <span className={inverted ? "loss" : ""}>{inverted ? "INVERTED" : "NORMAL"} · {fmtStamp(spread.date)}</span> : "UNAVAILABLE"}
      />
    </StatGrid>
  );
}
