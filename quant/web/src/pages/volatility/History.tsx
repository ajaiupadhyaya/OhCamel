/**
 * HISTORY (P7, vol.surface_history): each night's delayed Cboe snapshot fitted with SVI and
 * reduced to one row per underlying per day: ATM 30D / 90D (total-variance interpolation, no
 * extrapolation), the term slope, RR / BF 25Δ at 30D, 30D model-free variance, and the VRP
 * against the P2 league's HAR 22-day forecast (Lane M, plan M8). The history only grows; the
 * cell says how many days it holds. Before Lane M's first run: INSUFFICIENT DATA · NOT YET RUN.
 */
import { ArtifactCell, DataTable, Section, type Column } from "../../components";
import { XYChart } from "../../charts/XYChart";
import { Absent, Note } from "../../design";
import { KINDS, type Manifest } from "../../lib/artifacts";
import { fmtDate, fmtNum, fmtPct } from "../../lib/format";
import { useTable } from "../research/products";
import { historyFor, type HistoryErrorRow, type HistoryPoint, type HistoryRow, type HistorySummaryRow } from "./derive";
import { INFO } from "./info";
import { Readline, fmtVolPts } from "./shared";

export function HistoryView({ ticker }: { ticker: string }) {
  return (
    <Section
      title={
        <>
          Surface history · P7
          <Note n={1} to="p7-surface-history" />
        </>
      }
    >
      <ArtifactCell title="Daily · SVI · ATM · skew · VRP" kind={KINDS.surface} span="all">
        {(m) => <History m={m} ticker={ticker} />}
      </ArtifactCell>
    </Section>
  );
}

function History({ m, ticker }: { m: Manifest; ticker: string }) {
  const history = useTable<HistoryRow>(KINDS.surface, m, "history");
  const summary = useTable<HistorySummaryRow>(KINDS.surface, m, "summary");
  const errors = useTable<HistoryErrorRow>(KINDS.surface, m, "errors");
  if (history.block) return <>{history.block}</>;
  return <HistoryReport history={history.rows ?? []} summary={summary.rows ?? []} errors={errors.rows ?? []} ticker={ticker} />;
}

export function HistoryReport({ history, summary, errors, ticker }: { history: HistoryRow[]; summary: HistorySummaryRow[]; errors: HistoryErrorRow[]; ticker: string }) {
  const t = ticker.toUpperCase();
  const pts = historyFor(history, t);
  const days = new Set(history.map((r) => r.asof)).size;
  const last = pts[pts.length - 1];

  const histCols: Column<HistoryPoint>[] = [
    { key: "asof", label: "Date", render: (r) => <span className="num">{fmtDate(r.asof, "iso")}</span> },
    { key: "atm_iv_30d", label: "ATM 30D", numeric: true, format: (v) => fmtPct(v, 1), info: INFO.atm_30d },
    { key: "atm_iv_90d", label: "ATM 90D", numeric: true, format: (v) => fmtPct(v, 1) },
    { key: "term_slope", label: "Slope", numeric: true, format: (v) => fmtVolPts(v, 2), hideBelow: 600, title: "ATM 90D − 30D, vol pts" },
    { key: "rr25_30d", label: "RR 25Δ", numeric: true, format: (v) => fmtVolPts(v, 2), info: INFO.rr, hideBelow: 900 },
    { key: "bf25_30d", label: "BF 25Δ", numeric: true, format: (v) => fmtVolPts(v, 2), info: INFO.bf, hideBelow: 900 },
    { key: "mf_vol_30d", label: "MF 30D", numeric: true, format: (v) => fmtPct(v, 1), info: INFO.model_free },
    { key: "har_vol_22d", label: "HAR 22D", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 1200 },
    { key: "vrp_pts", label: "VRP · pts", numeric: true, render: (r) => <span className="num" title={r.vrp_note ?? undefined}>{r.vrp_pts === null ? "NO HAR" : fmtVolPts(r.vrp_pts, 1)}</span>, info: INFO.vrp },
    { key: "n_slices", label: "Slices", numeric: true, format: (v) => fmtNum(v, 0), hideBelow: 1200 },
  ];
  const sumCols: Column<HistorySummaryRow>[] = [
    { key: "underlying", label: "Underlying", render: (r) => <span className="num">{r.underlying}</span> },
    { key: "days", label: "Days", numeric: true, format: (v) => fmtNum(v, 0) },
    { key: "first", label: "First", render: (r) => <span className="num">{fmtDate(r.first, "iso")}</span> },
    { key: "last", label: "Last", render: (r) => <span className="num">{fmtDate(r.last, "iso")}</span> },
  ];
  const errCols: Column<HistoryErrorRow>[] = [
    { key: "underlying", label: "Underlying", render: (r) => <span className="num">{r.underlying}</span> },
    { key: "asof", label: "Date", render: (r) => <span className="num">{fmtDate(r.asof, "iso")}</span> },
    { key: "error", label: "Error", wrap: true, render: (r) => <span className="num">{r.error ?? "—"}</span> },
  ];

  return (
    <div className="vx-art-body">
      <Readline
        items={[
          { k: "DAYS", v: fmtNum(days, 0) },
          { k: "UNDERLYINGS", v: fmtNum(summary.length || new Set(history.map((r) => r.underlying)).size, 0) },
          { k: "ERRORS", v: fmtNum(errors.length, 0) },
        ]}
      />

      <h4 className="vx-table-title">
        {t} · {pts.length} {pts.length === 1 ? "DAY" : "DAYS"}
      </h4>
      {pts.length === 0 ? (
        <Absent reason={`NO SNAPSHOTS FOR ${t}`} source={`${KINDS.surface} · history`} />
      ) : (
        <>
          {last && (
            <Readline
              items={[
                { k: "LAST", v: fmtDate(last.asof, "iso") },
                { k: "ATM 30D", v: fmtPct(last.atm_iv_30d, 1) },
                { k: "MF 30D", v: fmtPct(last.mf_vol_30d, 1) },
                { k: "VRP", v: last.vrp_pts === null ? "NO HAR" : `${fmtVolPts(last.vrp_pts, 1)} PTS` },
              ]}
            />
          )}
          {pts.length >= 2 ? (
            <div className="grid-2">
              <XYChart
                time
                x={pts.map((p) => p.asof)}
                series={[
                  { name: "ATM 30D", y: pts.map((p) => p.atm_iv_30d), tone: "ink", width: 1.5 },
                  { name: "ATM 90D", y: pts.map((p) => p.atm_iv_90d), tone: "ink2", dash: "dash" },
                  { name: "MF 30D", y: pts.map((p) => p.mf_vol_30d), tone: "ink3" },
                ]}
                yFormat="pct"
                digits={1}
                height={260}
                ariaLabel={`${t} at-the-money implied volatility history`}
              />
              <XYChart
                time
                x={pts.map((p) => p.asof)}
                series={[
                  { name: "RR 25Δ", y: pts.map((p) => p.rr25_30d), tone: "ink" },
                  { name: "BF 25Δ", y: pts.map((p) => p.bf25_30d), tone: "ink2", dash: "dash" },
                  { name: "VRP", y: pts.map((p) => p.vrp_pts), tone: "ink3" },
                ]}
                yFormat="pct"
                digits={2}
                height={260}
                hlines={[{ at: 0, label: "0", tone: "ink2", dash: "solid" }]}
                ariaLabel={`${t} skew and variance risk premium history`}
              />
            </div>
          ) : (
            <Absent value="INSUFFICIENT DATA" reason="1 DAY · A LINE NEEDS 2" source={`${KINDS.surface} · history · ${t}`} />
          )}
          <DataTable<HistoryPoint> columns={histCols} rows={[...pts].reverse()} rowKey={(r) => r.asof} maxHeight={22 * 14} />
        </>
      )}

      {summary.length > 0 && (
        <>
          <h4 className="vx-table-title">Coverage · {summary.length}</h4>
          <DataTable<HistorySummaryRow> columns={sumCols} rows={summary} rowKey={(r) => r.underlying} maxHeight={22 * 12} />
        </>
      )}
      {errors.length > 0 && (
        <>
          <h4 className="vx-table-title">Errors · {errors.length}</h4>
          <DataTable<HistoryErrorRow> columns={errCols} rows={errors} rowKey={(r, i) => `${r.underlying}-${r.asof}-${i}`} maxHeight={22 * 8} />
        </>
      )}
    </div>
  );
}
