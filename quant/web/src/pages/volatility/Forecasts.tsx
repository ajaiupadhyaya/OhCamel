/**
 * FORECASTS (P2, vol.forecast_league): GARCH, GJR, EGARCH, EWMA and HAR-RV one-day variance
 * forecasts scored out of sample (last 250 sessions) on QLIKE, with a Model Confidence Set at
 * 10% per name and Diebold–Mariano per pair (Lane M, plan M3). The ArtifactCell stamps the
 * league's verdict first (DESCRIPTIVE ONLY: a measurement, not a strategy); then the models
 * across names, this name's scores, its next-day forecasts and DM pairs, and what was skipped.
 * Before Lane M's first run every cell reads INSUFFICIENT DATA · NOT YET RUN.
 */
import type { ReactNode } from "react";
import { ArtifactCell, DataTable, Section, type Column } from "../../components";
import { Absent, Note } from "../../design";
import { KINDS, type Manifest } from "../../lib/artifacts";
import { fmtDate, fmtNum, fmtPct } from "../../lib/format";
import { useTable } from "../research/products";
import { dmFor, leagueFor, leagueSummary, modelLabel, nameForecasts, type DmRow, type ForecastRow, type LeagueRow, type LeagueSummaryRow, type SkipRow } from "./derive";
import { Readline } from "./shared";

const QLIKE_INFO = { title: "QLIKE", text: "Mean of ln F + RV/F over the evaluation days: robust to a noisy realized-variance proxy. Lower is better.", reference: "Patton (2011), J. Econometrics 160(1)" };
const MCS_INFO = { title: "Model Confidence Set · 10%", text: "The set of models not beaten at the 10% level (range statistic, stationary bootstrap).", reference: "Hansen, Lunde & Nason (2011), Econometrica 79(2)" };
const DM_INFO = { title: "Diebold–Mariano", text: "t-stat of the mean QLIKE difference, Newey–West variance; negative: A has the smaller loss.", reference: "Diebold & Mariano (1995), JBES 13(3)" };

export function ForecastsView({ ticker }: { ticker: string }) {
  return (
    <Section
      title={
        <>
          Forecast league · P2
          <Note n={1} to="p2-vol-league" />
        </>
      }
    >
      <ArtifactCell title="Models · QLIKE · MCS 10% · all names" kind={KINDS.forecasts} span="all">
        {(m) => <League m={m} ticker={ticker} />}
      </ArtifactCell>
    </Section>
  );
}

function League({ m, ticker }: { m: Manifest; ticker: string }) {
  const summary = useTable<LeagueSummaryRow>(KINDS.forecasts, m, "summary");
  const league = useTable<LeagueRow>(KINDS.forecasts, m, "league");
  const dm = useTable<DmRow>(KINDS.forecasts, m, "dm");
  const fc = useTable<ForecastRow>(KINDS.forecasts, m, "forecasts");
  const skipped = useTable<SkipRow>(KINDS.forecasts, m, "skipped");
  const dropped = useTable<SkipRow>(KINDS.forecasts, m, "dropped");
  if (summary.block) return <>{summary.block}</>;
  return <LeagueReport summary={summary.rows ?? []} league={league.rows} dm={dm.rows} forecasts={fc.rows} skipped={skipped.rows ?? []} dropped={dropped.rows ?? []} ticker={ticker} pending={league.block ?? fc.block} />;
}

export function LeagueReport({ summary, league, dm, forecasts, skipped, dropped, ticker, pending }: { summary: LeagueSummaryRow[]; league: LeagueRow[] | null; dm: DmRow[] | null; forecasts: ForecastRow[] | null; skipped: SkipRow[]; dropped: SkipRow[]; ticker: string; pending?: ReactNode }) {
  const t = ticker.toUpperCase();
  const mine = league ? leagueFor(league, t) : [];
  const f = forecasts ? nameForecasts(forecasts, t) : null;
  const pairs = dm ? dmFor(dm, t) : [];
  const names = league ? new Set(league.map((r) => r.ticker)).size : null;

  const sumCols: Column<LeagueSummaryRow>[] = [
    { key: "model", label: "Model", render: (r) => <span className="num">{modelLabel(r.model)}</span> },
    { key: "mcs_rate", label: "In MCS", numeric: true, format: (v) => fmtPct(v, 0), info: MCS_INFO },
    { key: "mean_rank", label: "Mean rank", numeric: true, format: (v) => fmtNum(v, 2) },
    { key: "median_qlike", label: "Median QLIKE", numeric: true, format: (v) => fmtNum(v, 3), info: QLIKE_INFO },
    { key: "names", label: "Names", numeric: true, format: (v) => fmtNum(v, 0) },
  ];
  const nameCols: Column<LeagueRow>[] = [
    { key: "rank_qlike", label: "Rank", numeric: true, format: (v) => fmtNum(v, 0) },
    { key: "model", label: "Model", render: (r) => <span className="num">{modelLabel(r.model)}</span> },
    { key: "in_mcs", label: "MCS", render: (r) => <span className="num">{r.in_mcs === null ? "—" : r.in_mcs ? "IN" : "OUT"}</span>, info: MCS_INFO },
    { key: "qlike", label: "QLIKE", numeric: true, format: (v) => fmtNum(v, 4), info: QLIKE_INFO },
    { key: "mse", label: "MSE ×1E−8", numeric: true, format: (v) => (typeof v === "number" ? fmtNum(v * 1e8, 3) : "—"), hideBelow: 600 },
    { key: "n", label: "N", numeric: true, format: (v) => fmtNum(v, 0), hideBelow: 900 },
  ];
  const dmCols: Column<DmRow>[] = [
    { key: "a", label: "A", render: (r) => <span className="num">{modelLabel(r.a)}</span> },
    { key: "b", label: "B", render: (r) => <span className="num">{modelLabel(r.b)}</span> },
    { key: "stat", label: "DM t", numeric: true, format: (v) => fmtNum(v, 2, { signed: true }), info: DM_INFO },
    { key: "pvalue", label: "p", numeric: true, render: (r) => <span className={r.pvalue != null && r.pvalue < 0.05 ? "vx-strong" : ""}>{r.pvalue != null && r.pvalue < 0.001 ? "<0.001" : fmtNum(r.pvalue, 3)}</span> },
  ];
  const skipCols: Column<SkipRow>[] = [
    { key: "ticker", label: "Name", render: (r) => <span className="num">{r.ticker}</span> },
    ...(dropped.length ? [{ key: "model", label: "Model", render: (r: SkipRow) => <span className="num">{r.model ? modelLabel(r.model) : "—"}</span> }] : []),
    { key: "reason", label: "Reason", wrap: true, render: (r) => <span className="num">{r.reason ?? "—"}</span> },
  ];

  return (
    <div className="vx-art-body">
      <Readline items={[{ k: "NAMES", v: names === null ? "—" : fmtNum(names, 0) }, { k: "SKIPPED", v: fmtNum(skipped.length, 0) }, { k: "MODELS DROPPED", v: fmtNum(dropped.length, 0) }]} />
      <DataTable<LeagueSummaryRow> columns={sumCols} rows={leagueSummary(summary)} rowKey={(r) => r.model} />

      <h4 className="vx-table-title">{t} · out of sample</h4>
      {pending ? (
        pending
      ) : mine.length ? (
        <div className="vx-grid-main">
          <DataTable<LeagueRow> columns={nameCols} rows={mine} rowKey={(r) => r.model} />
          <div>
            {f ? (
              <dl className="vx-kv">
                {f.models.map((x) => (
                  <div className="vx-kv-row" key={x.model}>
                    <dt>{modelLabel(x.model)} · NEXT DAY · ANN</dt>
                    <dd className="num">{fmtPct(x.vol, 1)}</dd>
                  </div>
                ))}
                <div className="vx-kv-row">
                  <dt>HAR · 22D · ANN</dt>
                  <dd className="num">{f.har22 === null ? "NO MINUTE BARS" : fmtPct(f.har22, 1)}</dd>
                </div>
                <div className="vx-kv-row">
                  <dt>AS OF</dt>
                  <dd className="num">{fmtDate(f.asof).toUpperCase()}</dd>
                </div>
              </dl>
            ) : (
              <Absent reason="NO FORECAST ROW" source={`${KINDS.forecasts} · forecasts · ${t}`} />
            )}
            <div className="vx-target num">TARGET {mine[0].target === "minute_rv" ? "MINUTE RV" : "SQUARED DAILY RETURN"}</div>
          </div>
        </div>
      ) : (
        <Absent reason={`${t} NOT IN THE LEAGUE`} source={`${KINDS.forecasts} · league`} />
      )}

      {pairs.length > 0 && (
        <>
          <h4 className="vx-table-title">{t} · Diebold–Mariano · QLIKE</h4>
          <DataTable<DmRow> columns={dmCols} rows={pairs} rowKey={(r) => `${r.a}-${r.b}`} />
        </>
      )}

      {(skipped.length > 0 || dropped.length > 0) && (
        <>
          <h4 className="vx-table-title">Skipped · dropped · {skipped.length + dropped.length}</h4>
          <DataTable<SkipRow> columns={skipCols} rows={[...dropped, ...skipped]} rowKey={(r, i) => `${r.ticker}-${r.model ?? ""}-${i}`} maxHeight={22 * 12} />
        </>
      )}
    </div>
  );
}
