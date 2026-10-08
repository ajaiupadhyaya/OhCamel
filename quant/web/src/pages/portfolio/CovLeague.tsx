/**
 * COV LEAGUE (P3, cov.league): weekly league of covariance estimators scored by the realized
 * volatility of each estimator's minimum-variance portfolio over the next month, with the
 * Ledoit–Wolf (2008) test against the sample estimator. Verdict first (ArtifactCell); before
 * the product has run the cell reads INSUFFICIENT DATA · NOT YET RUN.
 */
import { ArtifactCell, BarChart, DataTable, Section, Skeleton, type Column } from "../../components";
import { Absent } from "../../design";
import { KINDS, useArtifactTable, type Manifest } from "../../lib/artifacts";
import { fmtNum, fmtPct } from "../../lib/format";
import { leagueRows, type LeagueRow } from "./league";

export function CovLeague() {
  return (
    <Section title="Covariance league · P3">
      <ArtifactCell title="Min-var OOS vol · 1M" kind={KINDS.covariance} span="all">
        {(m) => <LeagueBody m={m} />}
      </ArtifactCell>
    </Section>
  );
}

function LeagueBody({ m }: { m: Manifest }) {
  const has = !m.tables || m.tables.includes("league");
  const q = useArtifactTable(KINDS.covariance, m.id, "league", has);
  const src = `${KINDS.covariance}/${m.id}/league`;
  if (!has) return <Absent reason="NO LEAGUE TABLE" source={`${KINDS.covariance}/${m.id}`} />;
  if (q.isLoading) return <Skeleton height={160} />;
  if (q.isError) return <Absent reason="TABLE UNAVAILABLE" source={src} />;
  const rows = leagueRows(q.data);
  if (!rows) return <Absent reason="UNKNOWN TABLE SHAPE" source={src} />;
  if (!rows.length) return <Absent reason="NO ESTIMATORS" source={src} />;
  const cols: Column<LeagueRow>[] = [
    { key: "rank", label: "#", numeric: true, width: 28 },
    { key: "estimator", label: "Estimator", render: (r) => <span className="num">{r.estimator.toUpperCase()}</span> },
    { key: "oosVol", label: "OOS vol", numeric: true, format: (v) => fmtPct(v, 2), info: { text: "Annualized realized vol of the estimator's minimum-variance portfolio over the following month." } },
    { key: "vsSample", label: "vs sample", numeric: true, format: (v) => fmtPct(v, 2, { signed: true }) },
    { key: "lwP", label: "LW p", numeric: true, format: (v) => (v == null ? "—" : v < 0.001 ? "<0.001" : fmtNum(v, 3)), info: { text: "Ledoit–Wolf (2008) robust test against the sample estimator.", reference: "Ledoit & Wolf (2008), Journal of Empirical Finance 15(5)" } },
  ];
  return (
    <div className="grid-2">
      <BarChart horizontal x={rows.map((r) => r.estimator.toUpperCase())} y={rows.map((r) => r.oosVol)} yFormat="pct" digits={2} height={rows.length * 26 + 12} />
      <DataTable rows={rows} columns={cols} rowKey={(r) => r.estimator} compact />
    </div>
  );
}
