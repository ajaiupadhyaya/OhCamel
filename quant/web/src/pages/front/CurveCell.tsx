/**
 * CURVE: the par curve today vs 1M vs 1Y (GET /api/macro/curve) beside the 2y, the 10y and
 * 2s10s with its NORMAL / INVERTED stamp (GET /api/macro/series). Each half fails alone.
 */
import { Link } from "react-router-dom";
import { ChartSkeleton, ErrorState, Panel, StatGrid, StatTile } from "../../components";
import { fmtBps, fmtPctPoints } from "../../lib/format";
import { fmtStamp } from "../../design/stamp";
import { useCurve } from "../macro/CurveTab";
import type { CurveOut } from "../macro/types";
import { CurveChart, type CurveLine } from "./CurveChart";
import { lastPair, slope2s10s, useTreasuries } from "./data";

function curveLines(d: CurveOut): CurveLine[] {
  const out: CurveLine[] = [];
  for (const c of d.compare) if (!c.error && c.par) out.push({ key: c.label, label: c.label, tenors: c.par.tenors, yields: c.par.yields });
  out.push({ key: "today", label: "NOW", tenors: d.par.tenors, yields: d.par.yields });
  return out;
}

export function CurveCell() {
  const curve = useCurve(undefined, ["1M", "1Y"]);
  const tsy = useTreasuries();
  const slope = tsy.data ? slope2s10s(tsy.data.data) : null;
  const two = tsy.data ? lastPair(tsy.data.data, "DGS2") : null;
  const ten = tsy.data ? lastPair(tsy.data.data, "DGS10") : null;
  const asOf = curve.data?.date ?? slope?.date;
  const inverted = slope !== null && slope.bp < 0;

  return (
    <Panel
      title="CURVE · UST PAR"
      span={2}
      asOf={asOf}
      provenance={[...(curve.data?.provenance ?? []), ...(tsy.data?.provenance ?? [])]}
      actions={
        <Link to="/macro?tab=curve" className="oc-go">
          RATES →
        </Link>
      }
    >
      <div className="fp-curve-grid">
        <div className="fp-curve-plot">
          {curve.isLoading ? <ChartSkeleton height={240} /> : curve.isError ? <ErrorState error={curve.error} compact /> : curve.data ? <CurveChart lines={curveLines(curve.data)} /> : null}
        </div>
        <div className="fp-curve-side">
          {tsy.isError ? (
            <ErrorState error={tsy.error} compact />
          ) : (
            <>
              <StatGrid min={96}>
                <StatTile size="sm" label="2Y" loading={tsy.isLoading} value={two ? fmtPctPoints(two.last) : null} />
                <StatTile size="sm" label="10Y" loading={tsy.isLoading} value={ten ? fmtPctPoints(ten.last) : null} />
              </StatGrid>
              <div className="fp-slope">
                <div className="fp-label">2S10S</div>
                <div className={`fp-slope-val num ${inverted ? "loss" : ""}`}>
                  {slope ? fmtBps(slope.bp / 1e4, 0, { signed: true }).replace(/\s*bp$/, "") : "—"}
                  {slope && <span className="fp-unit">BP</span>}
                </div>
                {slope && (
                  <div className="fp-slope-foot">
                    <span className={`verdict ${inverted ? "verdict-fail" : ""}`}>{inverted ? "INVERTED" : "NORMAL"}</span>
                    <span className="num fp-dim">{fmtStamp(slope.date)}</span>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </Panel>
  );
}
