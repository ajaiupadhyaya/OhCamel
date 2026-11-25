/**
 * SURFACE LEAGUE (completion task 4.5, vol.surface_league, weekdays 21:10): per underlying, the per-slice SVI the
 * surface panel has shown against SSVI (power-law phi, free of calendar and butterfly arbitrage by construction),
 * on three scores -- in-sample RMSE (vol points), butterfly and calendar violations (one code for both models),
 * and the next-day RMSE (the previous snapshot day's fit on the latest day's quotes; blank with its reason when
 * there is none). Verdict DESCRIPTIVE ONLY: a measurement, no model is chosen by it. Source and as-of come from
 * the artifact (ArtifactCell).
 *
 * SurfaceModelLine reads the live surface's two models (GET /api/options/surface/{t}): SSVI's parameters against
 * the condition eta (1 + |rho|) <= 2, its RMSE, and both models' violations on this chain.
 */
import { ArtifactCell, DataTable, type Column } from "../../components";
import { Absent } from "../../design";
import { KINDS, type Manifest } from "../../lib/artifacts";
import { fmtDate, fmtNum } from "../../lib/format";
import { useTable } from "../research/products";
import { Readline } from "./shared";
import type { SSVIBlock, SurfaceLeagueRow, Violations } from "./types";

const fmtPts = (v: number | null | undefined) => (v == null || !Number.isFinite(v) ? "—" : fmtNum(v, 2));
const ratio = (n: number | null | undefined, of: number | null | undefined) => (n == null ? "—" : `${fmtNum(n, 0)} / ${fmtNum(of ?? 0, 0)}`);

/** The short label of a blank next-day cell; the full reason stays beside it. */
export const nextDayBlank = (note: string | null | undefined): string =>
  note?.includes("no earlier snapshot") ? "NO EARLIER DAY" : note?.includes("days back") ? "GAP" : "NOT SCORED";

export function SurfaceLeagueView({ ticker }: { ticker: string }) {
  return (
    <ArtifactCell title="Surface league · SVI vs SSVI" kind={KINDS.surfaceLeague} span="all">
      {(m) => <League m={m} ticker={ticker} />}
    </ArtifactCell>
  );
}

function League({ m, ticker }: { m: Manifest; ticker: string }) {
  const league = useTable<SurfaceLeagueRow>(KINDS.surfaceLeague, m, "league");
  if (league.block) return <>{league.block}</>;
  return <SurfaceLeagueReport rows={league.rows ?? []} ticker={ticker} />;
}

export function SurfaceLeagueReport({ rows, ticker }: { rows: SurfaceLeagueRow[]; ticker: string }) {
  const t = ticker.toUpperCase();
  const mine = rows.filter((r) => r.underlying?.toUpperCase() === t);
  if (!mine.length) return <Absent reason={`${t} NOT IN THE LEAGUE`} source={`${KINDS.surfaceLeague} · league`} />;
  const cols: Column<SurfaceLeagueRow>[] = [
    { key: "model", label: "Model", render: (r) => <span title={r.fit_note ?? r.params ?? undefined}>{r.model}</span> },
    { key: "rmse_vol_pts", label: "RMSE · pts", numeric: true, render: (r) => <span className="num">{fmtPts(r.rmse_vol_pts)}</span>, title: "in-sample, implied-vol points, every fitted quote pooled" },
    { key: "butterfly_violations", label: "Butterfly", numeric: true, render: (r) => <span className="num">{ratio(r.butterfly_violations, r.n_slices)}</span>, title: "slices with g < 0 on k in [-3, 3]" },
    { key: "calendar_violations", label: "Calendar", numeric: true, render: (r) => <span className="num">{ratio(r.calendar_violations, r.calendar_pairs)}</span>, title: "adjacent expiries whose total variance decreases somewhere on k in [-3, 3]" },
    {
      key: "next_day_rmse_vol_pts",
      label: "Next day · pts",
      numeric: true,
      render: (r) =>
        r.next_day_rmse_vol_pts == null ? (
          <span className="num" title={r.next_day_note ?? undefined}>
            {nextDayBlank(r.next_day_note)} · {r.next_day_note ?? "—"}
          </span>
        ) : (
          <span className="num" title={`${fmtNum(r.next_day_n ?? 0, 0)} quotes scored, ${fmtNum(r.next_day_unscored ?? 0, 0)} on expiries the earlier fit lacked`}>
            {fmtPts(r.next_day_rmse_vol_pts)} · FIT {fmtDate(r.next_day_fit_asof, "iso")}
          </span>
        ),
      wrap: true,
    },
  ];
  return (
    <div className="vx-art-body">
      <Readline items={[{ k: "UNDERLYING", v: t }, { k: "SNAPSHOT", v: fmtDate(mine[0].asof, "iso") }, { k: "QUOTES", v: fmtNum(mine[0].n_quotes, 0) }]} />
      <DataTable<SurfaceLeagueRow> columns={cols} rows={mine} rowKey={(r) => r.model} />
    </div>
  );
}

export function SurfaceModelLine({ ssvi, sviViolations }: { ssvi: SSVIBlock; sviViolations: Violations }) {
  if (!ssvi.fit || !ssvi.violations) return <Absent reason={`SSVI NOT FITTED · ${ssvi.error ?? "unknown"}`} source="options/surface · ssvi" />;
  const p = ssvi.fit.params;
  return (
    <Readline
      items={[
        { k: "SSVI ρ", v: fmtNum(p.rho, 3) },
        { k: "η", v: fmtNum(p.eta, 3) },
        { k: "γ", v: fmtNum(p.gamma, 3) },
        { k: "η(1+|ρ|) ≤ 2", v: fmtNum(p.eta_one_plus_abs_rho, 3) },
        { k: "SSVI RMSE", v: `${fmtPts(ssvi.fit.rmse_vol_pts)} PTS` },
        { k: "CALENDAR SVI · SSVI", v: `${ratio(sviViolations.calendar_violations, sviViolations.calendar_pairs)} · ${ratio(ssvi.violations.calendar_violations, ssvi.violations.calendar_pairs)}` },
        { k: "BUTTERFLY SVI · SSVI", v: `${ratio(sviViolations.butterfly_violations, sviViolations.n_slices)} · ${ratio(ssvi.violations.butterfly_violations, ssvi.violations.n_slices)}` },
      ]}
    />
  );
}
