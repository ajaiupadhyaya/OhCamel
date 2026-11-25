/**
 * SURFACE LEAGUE (completion task 4.5, vol.surface_league, weekdays 21:10): per underlying, the per-slice SVI the
 * surface panel has shown against SSVI (power-law phi, free of calendar and butterfly arbitrage by construction),
 * on three scores -- in-sample RMSE (vol points), butterfly and calendar violations (one code for both models;
 * calendar twice: within each pair's common quoted range, and on [-3, 3] including the extrapolated wings), and
 * the next-day IV RMSE (the previous session's fit on the latest day's quotes; blank with its reason when there
 * is none). Verdict DESCRIPTIVE ONLY: a measurement, no model is chosen by it. Source and as-of come from
 * the artifact (ArtifactCell).
 *
 * Heston (task 4.6, COS) is a third row: calibrated to the same identified-IV quotes, each priced as a European
 * Black-76 option on its expiry's forward (the chains are American), scored in IV points; its violations are
 * measured on its COS-priced implied total variance, so wing counts there are numerics of the expansion.
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
  note?.includes("no earlier snapshot") ? "NO EARLIER DAY" : note?.includes("sessions back") ? "NOT PREVIOUS SESSION" : "NOT SCORED";

export function SurfaceLeagueView({ ticker }: { ticker: string }) {
  return (
    <ArtifactCell title="Surface league · SVI vs SSVI vs Heston" kind={KINDS.surfaceLeague} span="all">
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
    {
      key: "model",
      label: "Model",
      render: (r) => (
        <span title={r.params ?? r.fit_note ?? undefined}>
          {r.model}
          {r.fit_note && r.rmse_vol_pts != null ? <span className="vx-note"> · {r.fit_note}</span> : null}
        </span>
      ),
      wrap: true,
    },
    { key: "rmse_vol_pts", label: "RMSE · pts", numeric: true, render: (r) => <span className="num">{fmtPts(r.rmse_vol_pts)}</span>, title: "in-sample, implied-vol points, every fitted quote pooled" },
    { key: "butterfly_violations", label: "Butterfly", numeric: true, render: (r) => <span className="num">{ratio(r.butterfly_violations, r.n_slices)}</span>, title: "slices with g < 0 on k in [-3, 3]" },
    { key: "calendar_violations_quoted", label: "Calendar · quoted", numeric: true, render: (r) => <span className="num">{ratio(r.calendar_violations_quoted, r.calendar_pairs_quoted)}</span>, title: "adjacent expiries whose total variance decreases within their common quoted k range" },
    { key: "calendar_violations", label: "Calendar · [−3, 3] incl. wings", numeric: true, render: (r) => <span className="num">{ratio(r.calendar_violations, r.calendar_pairs)}</span>, title: "adjacent expiries whose total variance decreases somewhere on k in [-3, 3], including the wings each smile extrapolates beyond its quotes" },
    {
      key: "next_day_rmse_vol_pts",
      label: "Next-day IV RMSE · pts",
      numeric: true,
      render: (r) =>
        r.next_day_rmse_vol_pts == null ? (
          <span className="num" title={r.next_day_note ?? undefined}>
            {nextDayBlank(r.next_day_note)} · {r.next_day_note ?? "—"}
          </span>
        ) : (
          <span className="num" title={`${fmtNum(r.next_day_unscored ?? 0, 0)} quotes unscored (expiries the earlier fit lacked)`}>
            {fmtPts(r.next_day_rmse_vol_pts)} · n {fmtNum(r.next_day_n, 0)} · FIT {fmtDate(r.next_day_fit_asof, "iso")}
          </span>
        ),
      wrap: true,
    },
  ];
  return (
    <div className="vx-art-body">
      <Readline items={[{ k: "UNDERLYING", v: t }, { k: "SNAPSHOT", v: fmtDate(mine[0].asof, "iso") }, { k: "QUOTES", v: fmtNum(mine[0].n_quotes, 0) }]} />
      <DataTable<SurfaceLeagueRow> columns={cols} rows={mine} rowKey={(r) => r.model} />
      {mine.some((r) => r.model.startsWith("Heston")) && (
        <p className="vx-note">
          HESTON IS FITTED TO THE SAME IDENTIFIED-IV QUOTES, EACH PRICED AS A EUROPEAN BLACK-76 OPTION ON ITS EXPIRY&apos;S FORWARD (THE CHAINS ARE AMERICAN), AND SCORED AS PRICED BY COS AT 1024 TERMS ON C1 ± 24√C2 (STRIKES PAST THE RANGE GET THEIR OWN); ITS VIOLATIONS ARE MEASURED ON THAT COS-PRICED SURFACE AT POINTS PRICED ABOVE 1E-8 OF THE FORWARD, SO ANY COUNT THERE IS THE EXPANSION&apos;S, NOT THE MODEL&apos;S · {KINDS.surfaceLeague} · AS OF {fmtDate(mine[0].asof, "iso")}
        </p>
      )}
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
        { k: "CALENDAR · QUOTED SVI · SSVI", v: `${ratio(sviViolations.calendar_violations_quoted, sviViolations.calendar_pairs_quoted)} · ${ratio(ssvi.violations.calendar_violations_quoted, ssvi.violations.calendar_pairs_quoted)}` },
        { k: "CALENDAR · [−3, 3] INCL. WINGS SVI · SSVI", v: `${ratio(sviViolations.calendar_violations, sviViolations.calendar_pairs)} · ${ratio(ssvi.violations.calendar_violations, ssvi.violations.calendar_pairs)}` },
        { k: "BUTTERFLY SVI · SSVI", v: `${ratio(sviViolations.butterfly_violations, sviViolations.n_slices)} · ${ratio(ssvi.violations.butterfly_violations, ssvi.violations.n_slices)}` },
      ]}
    />
  );
}
