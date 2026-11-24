/**
 * FARM (P4, farm.sweep): every literature strategy × each universe it suits, swept, selected
 * walk-forward and held to the charter's gates with DSR deflated by every trial the farm has ever run (the trial ledger); a row's Fill
 * column says whether it was scored at gross 1 filled at the next open (as the desk trades) or before that.
 * The ArtifactCell stamps the farm's verdict first; then the count (failures first and
 * largest), the leaderboard (verdict first on every row), the selected cell's regimes and
 * cost ladder, and the cells the farm skipped with their reason. Before Lane M's first run
 * the cell reads INSUFFICIENT DATA · NOT YET RUN.
 */
import { useState } from "react";
import { ArtifactCell, DataTable, Section, type Column } from "../../components";
import { Absent, Note } from "../../design";
import { KINDS, type Manifest } from "../../lib/artifacts";
import { fmtCurrency, fmtDate, fmtNum, fmtPct } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import { reasonCode, reasonNames } from "../../lib/reasons";
import { CHARTER, GATE_CODE, costRows, failedGates, farmCounts, paramsText, pboHigh, type CostRow } from "./derive";
import { useTable } from "./products";
import { Readline } from "./shared";
import type { Catalog, FarmBoardRow, FarmCellRow, RegimeRow } from "./types";

export function FarmView() {
  const catalog = useApiQuery<Catalog>("/backtest/strategies", undefined, { staleTime: Infinity });
  const names: Record<string, string> = {};
  for (const s of catalog.data?.strategies ?? []) names[s.key] = s.name;
  return (
    <Section
      title={
        <>
          Strategy farm · P4
          <Note n={1} to="p4-strategy-farm" />
        </>
      }
    >
      <ArtifactCell title="Leaderboard · charter gates · DSR over every trial" kind={KINDS.farm} span="all">
        {(m) => <FarmTables m={m} names={names} />}
      </ArtifactCell>
    </Section>
  );
}

function FarmTables({ m, names }: { m: Manifest; names: Record<string, string> }) {
  const board = useTable<FarmBoardRow>(KINDS.farm, m, "leaderboard");
  const cells = useTable<FarmCellRow>(KINDS.farm, m, "cells");
  const regimes = useTable<RegimeRow>(KINDS.farm, m, "regimes");
  if (board.block) return <>{board.block}</>;
  if (cells.block) return <>{cells.block}</>;
  return <FarmBoard board={board.rows ?? []} cells={cells.rows ?? []} regimes={regimes.rows ?? []} names={names} costs={costsSource(m)} />;
}

/** Where the cost figures come from: the one cost file (completion task 2.3), as the artifact's provenance names
 *  it -- its version, and how many of its tiers are provisional, so the word goes when the owner measures them. */
export function costsSource(m: Pick<Manifest, "provenance"> | null | undefined): string {
  const p = (m?.provenance ?? []).find((r) => r.source === "costs") as Record<string, unknown> | undefined;
  if (!p) return "research/config/costs.yaml";
  const prov = typeof p.provisional_tiers === "number" ? p.provisional_tiers : null;
  const total = typeof p.tiers === "number" ? p.tiers : null;
  const head = `${String(p.path ?? "research/config/costs.yaml")} v${String(p.version ?? "?")} — half-spread by tier, borrow, financing`;
  if (prov === null || total === null) return head;
  return prov > 0 ? `${head}; ${prov} of ${total} tiers provisional (a stated rule, not yet measured on quotes)` : `${head}; every tier measured`;
}

interface SweepRow {
  mult: number;
  sharpe: number | null;
}

/** The half-spread multiples a row carries (its ``sr_at_<m>x`` columns), in order: whatever the cost file's sweep was. */
export function sweepRows(row: FarmBoardRow): SweepRow[] {
  const out: SweepRow[] = [];
  for (const [k, v] of Object.entries(row)) {
    const hit = /^sr_at_(\d+(?:\.\d+)?)x$/.exec(k);
    if (hit) out.push({ mult: Number(hit[1]), sharpe: typeof v === "number" && Number.isFinite(v) ? v : null });
  }
  return out.sort((a, b) => a.mult - b.mult);
}

const verdictClass = (v: string | null | undefined) => (v === "FAIL" ? "sl-v sl-v-fail" : v === "PASS" ? "sl-v" : "sl-v sl-v-dim");
const universeLabel = (u: string | null | undefined) => (u ? u.replace(/_/g, " ").toUpperCase() : "—");

/** The configuration a leaderboard row was scored at (completion task 2.4), from its own fields. */
export function fillLabel(r: Pick<FarmBoardRow, "fill" | "max_gross">): string {
  if (r.fill === "open" && r.max_gross === 1) return "NEXT OPEN · 1×";
  if (r.fill == null) return "CLOSE · DEFAULT";
  return `${r.fill.toUpperCase()} · ${typeof r.max_gross === "number" ? `${fmtNum(r.max_gross, 1)}×` : "—"}`;
}

export function FarmBoard({ board, cells, regimes, names, costs = costsSource(null) }: { board: FarmBoardRow[]; cells: FarmCellRow[]; regimes: RegimeRow[]; names: Record<string, string>; costs?: string }) {
  const [sel, setSel] = useState<string | null>(null);
  const counts = farmCounts(board, cells);
  const active = board.find((r) => r.cell_id === sel) ?? board[0];
  const skipped = cells.filter((c) => c.status === "skipped");
  const nameOf = (k: string) => names[k] ?? k.replace(/_/g, " ").toUpperCase();

  const cols: Column<FarmBoardRow>[] = [
    { key: "verdict", label: "Verdict", render: (r) => <span className={verdictClass(r.verdict)}>{r.verdict}</span> },
    { key: "strategy", label: "Strategy", value: (r) => nameOf(r.strategy), render: (r) => <span>{nameOf(r.strategy)}</span> },
    { key: "universe", label: "Universe", hideBelow: 900, render: (r) => <span className="num">{universeLabel(r.universe)}</span> },
    { key: "dsr_farm", label: "DSR", numeric: true, format: (v) => fmtNum(v, 2), color: (v) => (typeof v === "number" && v < CHARTER.dsr ? "loss" : undefined), info: { text: `Deflated Sharpe, counting every trial the farm has ever run (the append-only trial ledger). Gate ≥ ${fmtNum(CHARTER.dsr, 2)}.`, reference: "Bailey & López de Prado (2014), Journal of Portfolio Management 40(5)" } },
    { key: "fill", label: "Fill", hideBelow: 900, value: (r) => fillLabel(r), render: (r) => <span className="num">{fillLabel(r)}</span>, info: { text: "What the row was scored at. NEXT OPEN · 1×: decided at a close, filled at the next session's open at gross 1, as the desk trades. CLOSE · DEFAULT: scored before that, at the strategy's default leverage and filled at the close; it changes when the cell re-runs." } },
    { key: "psr", label: "PSR", numeric: true, format: (v) => fmtNum(v, 2), color: (v) => (typeof v === "number" && v < CHARTER.psr ? "loss" : undefined), info: { text: `P(true Sharpe > 0). Gate ≥ ${fmtNum(CHARTER.psr, 2)}.` } },
    { key: "oos_sharpe_ann", label: "SR OOS", numeric: true, format: (v) => fmtNum(v, 2), info: { text: "Annualized Sharpe of the stitched walk-forward record (5Y in, 1Y out)." } },
    { key: "holdout_return", label: "Holdout", numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), color: "sign" },
    { key: "boot_lo5", label: "Boot lo 5%", numeric: true, hideBelow: 1200, format: (v) => fmtNum(v, 2), color: "sign", info: { text: "Lower 5th percentile of the stationary-bootstrap Sharpe. Gate > 0." } },
    { key: "pbo", label: "PBO", numeric: true, hideBelow: 900, format: (v) => fmtNum(v, 2), info: { text: `CSCV probability of backtest overfitting, S = 16. Reported; > ${fmtNum(CHARTER.pboHigh, 1)} is marked high.` } },
    { key: "spa_p", label: "SPA p", numeric: true, hideBelow: 1200, format: (v) => fmtNum(v, 3), info: { text: "Hansen's SPA p-value against equal-weight buy-and-hold. Reported." } },
    { key: "turnover", label: "Turnover", numeric: true, hideBelow: 1200, format: (v) => fmtNum(v, 1), info: { text: "One-way turnover a year (Σ|Δw|) along the out-of-sample record, fold switches included.", reference: costs } },
    { key: "capacity_usd", label: "Capacity", numeric: true, hideBelow: 900, format: (v) => fmtCurrency(v as number | null, { compact: true }), info: { text: "Capital at which the largest out-of-sample trade is 1% of its name's 20-session median dollar volume. Blank: no volume history for a trade.", reference: costs } },
    { key: "failed", label: "Failed", value: (r) => failedGates(r.detail).length, render: (r) => <FailedCell detail={r.detail} /> },
  ];

  return (
    <div className="sl-art-body">
      <div className="sl-count num" aria-label="Farm count">
        <span className={`sl-count-item sl-count-big ${counts.fail > 0 ? "sl-count-fail" : ""}`}>
          <span className="sl-count-n">{counts.fail}</span>
          <span className="sl-count-w">FAIL</span>
        </span>
        <span className="sl-count-item">
          <span className="sl-count-n">{counts.pass}</span>
          <span className="sl-count-w">PASS</span>
        </span>
        {counts.insufficient > 0 && (
          <span className="sl-count-item">
            <span className="sl-count-n">{counts.insufficient}</span>
            <span className="sl-count-w">INSUFFICIENT</span>
          </span>
        )}
        <span className="sl-count-item">
          <span className="sl-count-n">{counts.skipped}</span>
          <span className="sl-count-w">SKIPPED</span>
        </span>
        <span className="sl-count-item">
          <span className="sl-count-n">{counts.total}</span>
          <span className="sl-count-w">CELLS</span>
        </span>
      </div>

      {board.length ? (
        <DataTable<FarmBoardRow> columns={cols} rows={board} rowKey={(r) => r.cell_id} compact onRowClick={(r) => setSel(r.cell_id)} isActive={(r) => r.cell_id === active?.cell_id} maxHeight={22 * 16} />
      ) : (
        <Absent reason="NO CELL RAN" source={`${KINDS.farm} · leaderboard`} />
      )}

      {active && <CellDetail row={active} cell={cells.find((c) => c.cell_id === active.cell_id)} regimes={regimes.filter((r) => r.cell_id === active.cell_id)} name={nameOf(active.strategy)} costsRef={costs} />}

      {skipped.length > 0 && (
        <div>
          <h3 className="sl-sub">SKIPPED · {skipped.length}</h3>
          <DataTable<FarmCellRow>
            columns={[
              { key: "strategy", label: "Strategy", render: (c) => <span className="num">{c.strategy.replace(/_/g, " ").toUpperCase()}</span> },
              { key: "universe", label: "Universe", render: (c) => <span className="num">{universeLabel(c.universe)}</span> },
              { key: "reason", label: "Reason", value: (c) => reasonCode(c.reason), render: (c) => <span className="num sl-gate-dim" title={c.reason ?? undefined}>{reasonCode(c.reason)}</span> },
              { key: "names", label: "Names", numeric: true, value: (c) => reasonNames(c.reason).length, render: (c) => <span className="num" title={reasonNames(c.reason).join(", ") || undefined}>{reasonNames(c.reason).length ? fmtNum(reasonNames(c.reason).length, 0) : "—"}</span> },
            ]}
            rows={skipped}
            rowKey={(c) => c.cell_id}
            compact
          />
        </div>
      )}
    </div>
  );
}

function FailedCell({ detail }: { detail: string | null }) {
  const failed = failedGates(detail).map((g) => GATE_CODE[g] ?? g.toUpperCase());
  const high = pboHigh(detail);
  if (!failed.length && !high) return <span className="sl-gates num sl-gate-dim">—</span>;
  return (
    <span className="sl-gates num">
      {failed.length > 0 && <span className="sl-gate-fail">{failed.join(" · ")}</span>}
      {high && <span className="sl-gate-dim">{failed.length ? " · " : ""}PBO HIGH</span>}
    </span>
  );
}

function CellDetail({ row, cell, regimes, name, costsRef }: { row: FarmBoardRow; cell?: FarmCellRow; regimes: RegimeRow[]; name: string; costsRef: string }) {
  const costs = costRows(cell?.cost_curve);
  const sweep = sweepRows(row);
  return (
    <div>
      <Readline
        items={[
          { k: "CELL", v: `${name.toUpperCase()} · ${universeLabel(row.universe)}` },
          { k: "PICK", v: paramsText(row.selected_params) },
          { k: "GRID", v: row.n_combos ?? "—" },
          { k: "DATA", v: fmtDate(row.data_asof) },
          { k: "RAN", v: fmtDate(row.ran_at) },
        ]}
      />
      <div className="sl-detail">
        <div>
          <h3 className="sl-sub">REGIMES · ≥ {CHARTER.regimes} OF {CHARTER.regimesOf} POSITIVE</h3>
          {regimes.length ? (
            <DataTable<RegimeRow>
              columns={[
                { key: "regime", label: "Regime", render: (r) => <span className="num">{r.regime}</span> },
                { key: "n", label: "N", numeric: true, format: (v) => fmtNum(v, 0) },
                { key: "total_return", label: "Return", numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), color: "sign" },
              ]}
              rows={regimes}
              rowKey={(r) => r.regime}
              compact
            />
          ) : (
            <Absent reason="NO REGIME ROWS" source={`${KINDS.farm} · regimes`} />
          )}
        </div>
        <div>
          <h3 className="sl-sub">COSTS · BP ONE WAY · OOS</h3>
          {costs.length ? (
            <DataTable<CostRow>
              columns={[
                { key: "cost_bps", label: "BP", numeric: true, format: (v) => fmtNum(v, 0) },
                { key: "sharpe", label: "SR", numeric: true, format: (v) => fmtNum(v, 2), color: "sign" },
                { key: "cagr", label: "CAGR", numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), color: "sign" },
              ]}
              rows={costs}
              rowKey={(c) => c.cost_bps}
              compact
            />
          ) : (
            <Absent reason="NO COST LADDER" source={`${KINDS.farm} · cells`} />
          )}
        </div>
        <div>
          <h3 className="sl-sub">SPREAD SWEEP · × HALF-SPREAD · OOS</h3>
          {sweep.some((r) => r.sharpe !== null) ? (
            <DataTable<SweepRow>
              columns={[
                { key: "mult", label: "×", numeric: true, format: (v) => `${fmtNum(v, 0)}×` },
                { key: "sharpe", label: "SR", numeric: true, format: (v) => fmtNum(v, 2), color: "sign", info: { text: "Annualized Sharpe of the out-of-sample record with every half-spread multiplied.", reference: costsRef } },
              ]}
              rows={sweep}
              rowKey={(r) => r.mult}
              compact
            />
          ) : (
            <Absent reason="NO SPREAD SWEEP" source={`${KINDS.farm} · leaderboard`} />
          )}
        </div>
        <div>
          <h3 className="sl-sub">GATES · AS WRITTEN</h3>
          <p className="sl-gate-line num">{row.detail ?? "—"}</p>
        </div>
      </div>
    </div>
  );
}
