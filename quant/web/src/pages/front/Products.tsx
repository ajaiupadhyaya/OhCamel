/**
 * The product cells, each reading its job kind's latest artifact and then its tables
 * (compute plan Lane M, contract II.3). Verdict first (ArtifactCell), then:
 *   REGIME        regime.hmm · probs        filtered P(high vol) now and two years of weeks
 *   RISK ATLAS    risk.mc_atlas · summary    core book 99% · 1D VaR / ES, FHS and t-copula
 *   STRATEGY FARM farm.sweep · leaderboard  n FAIL · m PASS, failures first and largest;
 *                                           the top three by farm DSR, verdict first
 *   MODELS        models.xs_lgbm · holdout  rank IC and its HAC t on the once-only holdout
 * An empty leaderboard shows the counts only: the verdict line above says why; when every
 * cell was skipped the headline is the skip count.
 * A product that has not run reads INSUFFICIENT DATA · NOT YET RUN; a product whose own
 * verdict is INSUFFICIENT DATA shows that verdict and its reason, and no number.
 */
import { ArtifactCell, DataTable, StatGrid, StatTile, type Column } from "../../components";
import { XYChart } from "../../charts/XYChart";
import { Absent } from "../../design";
import { fmtStamp } from "../../design/stamp";
import { KINDS, type Manifest } from "../../lib/artifacts";
import { fmtCurrency, fmtDate, fmtNum, fmtPct } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import { farmCounts } from "../research/derive";
import { useTable } from "../research/products";
import type { Catalog, FarmBoardRow, FarmCellRow, HoldoutRow } from "../research/types";
import { atlasFront, farmTop, regimeFront, type AtlasLeg } from "./readers";

const has = (m: Manifest, t: string) => !m.tables || m.tables.includes(t);

// ------------------------------------------------------------------ REGIME · P6
export function RegimeCell() {
  return (
    <ArtifactCell title="REGIME · HMM · P(HIGH VOL)" kind={KINDS.regimes} experiment="EXP-Q02" to="/macro?tab=regimes" go="RATES">
      {(m) => (has(m, "probs") ? <RegimeBody m={m} /> : null)}
    </ArtifactCell>
  );
}

export function RegimeBody({ m }: { m: Manifest }) {
  const t = useTable<Record<string, unknown>>(KINDS.regimes, m, "probs");
  if (t.block) return <>{t.block}</>;
  return <RegimeReport rows={t.rows ?? []} source={`${KINDS.regimes}/${m.id}/probs`} />;
}

export function RegimeReport({ rows, source }: { rows: Record<string, unknown>[]; source: string }) {
  const r = regimeFront(rows);
  if (!r) return <Absent reason="NO FILTERED WEEK" source={source} />;
  return (
    <div className="fp-art">
      <StatGrid min={120}>
        <StatTile size="lg" label="P(HIGH VOL)" value={fmtPct(r.last.p_high, 0)} caption={`FILTERED · WK ${fmtStamp(r.last.date)}`} />
        <StatTile size="sm" label="STATE" value={r.state} caption="AT 50%" />
      </StatGrid>
      {r.window.length > 1 && (
        <XYChart
          x={r.window.map((w) => w.date)}
          time
          series={[{ name: "FILTERED", y: r.window.map((w) => w.p_high), mode: "bars", tone: "ink" }]}
          hlines={[{ at: 0.5, label: "50%", tone: "ink3", dash: "dot" }]}
          zero
          yMin={0}
          yFormat="pct"
          digits={0}
          height={150}
          ariaLabel={`Filtered probability of the high-volatility state, weekly, last ${r.window.length} weeks`}
        />
      )}
    </div>
  );
}

// ------------------------------------------------------------------ RISK ATLAS · P1
export function AtlasCell() {
  return (
    <ArtifactCell title="RISK ATLAS · CORE · 99 · 1D" kind={KINDS.atlas} to="/risk" go="RISK">
      {(m) => <AtlasBody m={m} />}
    </ArtifactCell>
  );
}

const usd = (v: number | null) => (v === null ? undefined : fmtCurrency(v, { digits: 0 }));

function legTiles(prefix: string, leg: AtlasLeg | null) {
  return [
    <StatTile key={`${prefix}v`} size="sm" label={`${prefix} VAR`} info={prefix === "FHS" ? "var" : undefined} value={leg ? fmtPct(leg.var, 2) : null} caption={leg ? usd(leg.varUsd) : "NOT RUN"} />,
    <StatTile key={`${prefix}e`} size="sm" label={`${prefix} ES`} info={prefix === "FHS" ? "es" : undefined} value={leg ? fmtPct(leg.es, 2) : null} caption={leg ? usd(leg.esUsd) : "NOT RUN"} />,
  ];
}

function AtlasBody({ m }: { m: Manifest }) {
  const t = useTable<Record<string, unknown>>(KINDS.atlas, m, "summary");
  if (t.block) return <>{t.block}</>;
  return <AtlasReport rows={t.rows} source={`${KINDS.atlas}/${m.id}/summary`} />;
}

export function AtlasReport({ rows, source }: { rows: Record<string, unknown>[] | null; source: string }) {
  const a = atlasFront(rows);
  if (!a) return <Absent reason="NO RUN AT 99 · 1D" source={source} />;
  return (
    <div className="fp-art">
      <StatGrid min={150}>
        {legTiles("FHS", a.fhs)}
        {legTiles("T-COP", a.tcop)}
      </StatGrid>
      <div className="fp-meta num">
        BOOK {a.book.toUpperCase()}
        {a.notional !== null && ` · NOTIONAL ${fmtCurrency(a.notional, { digits: 0 })}`} · {fmtNum(a.books, 0)} {a.books === 1 ? "BOOK" : "BOOKS"} ON /RISK
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ STRATEGY FARM · P4
export function FarmCell() {
  return (
    <ArtifactCell title="STRATEGY FARM · DSR" kind={KINDS.farm} to="/research?view=farm" go="RSCH">
      {(m) => <FarmBody m={m} />}
    </ArtifactCell>
  );
}

function FarmBody({ m }: { m: Manifest }) {
  const board = useTable<FarmBoardRow>(KINDS.farm, m, "leaderboard");
  const cells = useTable<FarmCellRow>(KINDS.farm, m, "cells");
  const catalog = useApiQuery<Catalog>("/backtest/strategies", undefined, { staleTime: Infinity });
  if (board.block) return <>{board.block}</>;
  const names: Record<string, string> = {};
  for (const s of catalog.data?.strategies ?? []) names[s.key] = s.name;
  return <FarmReport board={board.rows ?? []} cells={cells.rows ?? []} names={names} />;
}

const verdictClass = (v: string) => (v === "FAIL" ? "fp-v fp-v-fail" : v === "PASS" ? "fp-v" : "fp-v fp-dim");

export function FarmReport({ board, cells, names }: { board: FarmBoardRow[]; cells: FarmCellRow[]; names: Record<string, string> }) {
  const c = farmCounts(board, cells);
  // Nothing tested (every cell skipped): the headline is the skip count, not 0 FAIL · 0 PASS.
  const allSkipped = board.length === 0 && c.skipped > 0;
  const cols: Column<FarmBoardRow>[] = [
    { key: "verdict", label: "VERDICT", sortable: false, render: (r) => <span className={verdictClass(r.verdict)}>{r.verdict}</span> },
    { key: "strategy", label: "STRATEGY", sortable: false, render: (r) => <span>{(names[r.strategy] ?? r.strategy.replace(/_/g, " ")).toUpperCase()}</span> },
    { key: "universe", label: "UNIV", sortable: false, hideBelow: 1600, render: (r) => <span className="num">{r.universe.replace(/_/g, " ").toUpperCase()}</span> },
    { key: "dsr_farm", label: "DSR", numeric: true, sortable: false, format: (v) => fmtNum(v, 2) },
  ];
  return (
    <div className="fp-art">
      {allSkipped ? (
        <div className="fp-farm" aria-label="Farm count">
          <span className="fp-farm-fail num fp-farm-zero">{fmtNum(c.skipped, 0)}</span>
          <span className="fp-farm-word">SKIPPED</span>
          <span className="fp-farm-word fp-dim">OF {fmtNum(c.total, 0)} CELLS</span>
        </div>
      ) : (
        <div className="fp-farm" aria-label="Farm count">
          <span className={`fp-farm-fail num ${c.fail ? "" : "fp-farm-zero"}`}>{fmtNum(c.fail, 0)}</span>
          <span className="fp-farm-word">FAIL</span>
          <span className="fp-farm-pass num">{fmtNum(c.pass, 0)}</span>
          <span className="fp-farm-word fp-dim">PASS</span>
          {c.skipped > 0 && (
            <>
              <span className="fp-farm-pass num fp-dim">{fmtNum(c.skipped, 0)}</span>
              <span className="fp-farm-word fp-dim">SKIPPED</span>
            </>
          )}
        </div>
      )}
      {board.length > 0 && <DataTable<FarmBoardRow> columns={cols} rows={farmTop(board)} rowKey={(r) => r.cell_id} compact />}
    </div>
  );
}

// ------------------------------------------------------------------ MODELS · P5
export function ModelsCell() {
  return (
    <ArtifactCell title="MODELS · EXP-Q01 · RANK IC" kind={KINDS.models} experiment="EXP-Q01" note={{ n: 1, to: "p5-exp-q01" }} to="/research?view=models" go="RSCH">
      {(m) => (has(m, "holdout") ? <ModelsBody m={m} /> : null)}
    </ArtifactCell>
  );
}

function ModelsBody({ m }: { m: Manifest }) {
  const t = useTable<HoldoutRow>(KINDS.models, m, "holdout");
  if (t.block) return <>{t.block}</>;
  return <ModelsReport h={t.rows?.[0] ?? null} source={`${KINDS.models}/${m.id}/holdout`} />;
}

export function ModelsReport({ h, source }: { h: HoldoutRow | null; source: string }) {
  if (!h) return <Absent reason="HOLDOUT ROW EMPTY" source={source} />;
  return (
    <div className="fp-art">
      <StatGrid min={110}>
        <StatTile size="lg" label="RANK IC" value={fmtNum(h.rank_ic_mean, 3)} caption="MEAN · MONTHLY" />
        <StatTile size="lg" label="t · HAC" value={fmtNum(h.rank_ic_hac_t, 2)} caption="NEWEY-WEST" />
      </StatGrid>
      <div className="fp-meta num">
        HOLDOUT {fmtDate(h.holdout_start).toUpperCase()} – {fmtDate(h.holdout_end).toUpperCase()} · NET SR {fmtNum(h.net_sharpe, 2)} · LIN {fmtNum(h.composite_net_sharpe, 2)}
      </div>
    </div>
  );
}
