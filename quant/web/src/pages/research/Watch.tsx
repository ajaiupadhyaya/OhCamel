/**
 * WATCH: the three things the droplet does on its own each night or week. None of them sizes
 * anything or decides anything; each reports what it saw.
 *
 *   kronos.shadow       Kronos (FAILED EXP-K01) and the league's incumbents forecast the next
 *                       session before it exists; realised rows are scored by QLIKE.
 *   factors.screen      every expression in the owner's trial ledger, re-screened on the
 *                       warehouse universe, exploration window only, against Alpha158.
 *   ops.data_integrity  unadjusted splits, bad prints, non-positive prices, calendar faults and
 *                       stale series, in the warehouse and the committed fixtures.
 */
import { ArtifactCell, DataTable, Section } from "../../components";
import { Absent, Note } from "../../design";
import { KINDS, type Manifest } from "../../lib/artifacts";
import { fmtDate, fmtNum } from "../../lib/format";
import { useTable } from "./products";
import { Figs, Readline, finite, type Fig } from "./shared";

export interface ShadowScore { symbol: string; model: string; n: number; qlike: number | null; rank: number }
export interface ShadowPooled { days: number; mean_qlike_diff_vs_ewma?: number | null; stat?: number | null; p_one_sided?: number | null }
export interface ShadowTonight { symbol: string; asof: string; model: string; var: number | null }
export interface ScreenRow {
  hash: string; expression: string; hypothesis?: string; lint_ok: boolean; failed?: string; duplicate_of?: string | null;
  rank_ic_mean?: number | null; rank_ic_hac_t?: number | null; rank_autocorr_lag1?: number | null; long_short_hac_t?: number | null; dates?: number | null;
}
export interface Finding { source: string; ticker: string; date: string; check: string; detail: string }

const MODELS = ["kronos", "garch", "gjr", "egarch", "ewma"];
/** Annualised volatility from a one-day variance forecast, for reading tonight's rows. */
export const annVol = (v: number | null) => (finite(v) && v > 0 ? Math.sqrt(252 * v) : null);

export function WatchView() {
  return (
    <>
      <Section title={<>Watch · Kronos shadow<Note n={1} to="k01-kronos-shadow" /></>}>
        <ArtifactCell title="Prospective volatility forecasts · Kronos vs incumbents" kind={KINDS.shadow} span="all">
          {(m) => <ShadowTables m={m} />}
        </ArtifactCell>
      </Section>
      <Section title={<>Watch · Factor ledger<Note n={2} to="factor-workbench" /></>}>
        <ArtifactCell title="Owner's trials re-screened · warehouse universe · to 2023-12-31" kind={KINDS.screen} span="all">
          {(m) => <ScreenTables m={m} />}
        </ArtifactCell>
      </Section>
      <Section title={<>Watch · Data integrity<Note n={3} to="data-integrity" /></>}>
        <ArtifactCell title="Every daily series · warehouse + committed fixtures" kind={KINDS.integrity} span="all">
          {(m) => <IntegrityTables m={m} />}
        </ArtifactCell>
      </Section>
    </>
  );
}

function ShadowTables({ m }: { m: Manifest }) {
  const scores = useTable<ShadowScore>(KINDS.shadow, m, "scores");
  const pooled = useTable<ShadowPooled>(KINDS.shadow, m, "pooled");
  const tonight = useTable<ShadowTonight>(KINDS.shadow, m, "tonight");
  if (scores.block) return <>{scores.block}</>;
  return <ShadowReport scores={scores.rows ?? []} pooled={pooled.rows?.[0] ?? null} tonight={tonight.rows ?? []} asof={m.data_asof ?? null} />;
}

/** Mean QLIKE by symbol and model, wide; lower is better. */
export function shadowWide(scores: ShadowScore[]): Record<string, string | number | null>[] {
  const by = new Map<string, Record<string, string | number | null>>();
  for (const s of scores) {
    const row = by.get(s.symbol) ?? { symbol: s.symbol, n: s.n };
    row[s.model] = s.qlike;
    by.set(s.symbol, row);
  }
  return [...by.values()];
}

export function ShadowReport({ scores, pooled, tonight, asof }: { scores: ShadowScore[]; pooled: ShadowPooled | null; tonight: ShadowTonight[]; asof: string | null }) {
  const kr = scores.filter((s) => s.model === "kronos");
  const meanRank = kr.length ? kr.reduce((a, s) => a + s.rank, 0) / kr.length : null;
  const figs: Fig[] = [
    { k: "DAYS", v: fmtNum(pooled?.days ?? 0, 0), sub: "PROSPECTIVE · REALISED" },
    { k: "KRONOS RANK", v: fmtNum(meanRank, 1), sub: `OF ${MODELS.length} · MEAN QLIKE` },
    { k: "DM VS EWMA", v: fmtNum(pooled?.stat ?? null, 2), sub: `p ${fmtNum(pooled?.p_one_sided ?? null, 3)} · ONE-SIDED`, tone: finite(pooled?.stat ?? null) && (pooled?.stat ?? 0) > 0 ? "loss" : "" },
  ];
  const wide = shadowWide(scores);
  const kronosTonight = tonight.filter((t) => t.model === "kronos");
  return (
    <div className="sl-art-body">
      <Readline items={[{ k: "STATUS", v: "SHADOW · FAILED EXP-K01 · SIZES NOTHING" }, { k: "LAST BAR", v: asof ? fmtDate(asof) : "—" }]} />
      <Figs items={figs} />
      <h3 className="sl-sub">MEAN QLIKE · LOWER IS BETTER · DAYS EVERY MODEL FORECAST</h3>
      {wide.length ? (
        <DataTable<Record<string, string | number | null>>
          columns={[
            { key: "symbol", label: "Name", render: (r) => <span className="num">{String(r.symbol)}</span> },
            { key: "n", label: "N", numeric: true, format: (v) => fmtNum(v as number, 0) },
            ...MODELS.map((mo) => ({ key: mo, label: mo.toUpperCase(), numeric: true, format: (v: unknown) => fmtNum(v as number, 3) })),
          ]}
          rows={wide}
          rowKey={(r) => String(r.symbol)}
          compact
        />
      ) : (
        <Absent reason="NO REALISED DAYS YET" source={`${KINDS.shadow} · scores`} />
      )}
      <h3 className="sl-sub">TONIGHT · KRONOS · NEXT-SESSION VOLATILITY, ANNUALISED</h3>
      {kronosTonight.length ? (
        <DataTable<ShadowTonight>
          columns={[
            { key: "symbol", label: "Name", render: (t) => <span className="num">{t.symbol}</span> },
            { key: "asof", label: "After", render: (t) => <span className="num">{fmtDate(t.asof)}</span> },
            { key: "var", label: "Vol", numeric: true, format: (v) => fmtNum(annVol(v as number | null), 3) },
          ]}
          rows={kronosTonight}
          rowKey={(t) => t.symbol}
          compact
        />
      ) : (
        <Absent reason="NO KRONOS ROWS TONIGHT" source={`${KINDS.shadow} · tonight`} />
      )}
    </div>
  );
}

function ScreenTables({ m }: { m: Manifest }) {
  const s = useTable<ScreenRow>(KINDS.screen, m, "screens");
  if (s.block) return <>{s.block}</>;
  return <ScreenReport rows={s.rows ?? []} />;
}

export function ScreenReport({ rows }: { rows: ScreenRow[] }) {
  if (!rows.length) return <Absent reason="NO TRIALS IN THE LEDGER" source={`${KINDS.screen} · screens`} />;
  return (
    <div className="sl-art-body">
      <Readline items={[{ k: "TRIALS", v: `${rows.length} DISTINCT = N FOR ANY DSR` }, { k: "WINDOW", v: "EXPLORATION ONLY · REPORTED, NOT GATED" }]} />
      <DataTable<ScreenRow>
        columns={[
          { key: "expression", label: "Expression", render: (r) => <span className="num" title={r.hypothesis ?? ""}>{r.expression}</span> },
          { key: "lint_ok", label: "Lint", render: (r) => <span className="num">{r.lint_ok ? "OK" : "FAIL"}</span> },
          { key: "duplicate_of", label: "Dup of", render: (r) => <span className="num">{r.duplicate_of ?? "—"}</span> },
          { key: "rank_ic_mean", label: "Rank IC", numeric: true, format: (v) => fmtNum(v as number | null, 4), color: "sign" },
          { key: "rank_ic_hac_t", label: "t", numeric: true, format: (v) => fmtNum(v as number | null, 2) },
          { key: "rank_autocorr_lag1", label: "Rank AC", numeric: true, format: (v) => fmtNum(v as number | null, 2) },
          { key: "long_short_hac_t", label: "L/S t", numeric: true, format: (v) => fmtNum(v as number | null, 2) },
        ]}
        rows={rows}
        rowKey={(r) => r.hash}
        compact
      />
    </div>
  );
}

function IntegrityTables({ m }: { m: Manifest }) {
  const f = useTable<Finding>(KINDS.integrity, m, "findings");
  if (f.block) return <>{f.block}</>;
  return <IntegrityReport findings={f.rows ?? []} />;
}

const SERIOUS = new Set(["split_suspect", "bad_print", "nonpositive", "calendar"]);

export function IntegrityReport({ findings }: { findings: Finding[] }) {
  const serious = findings.filter((f) => SERIOUS.has(f.check));
  const rest = findings.filter((f) => !SERIOUS.has(f.check));
  const cols = [
    { key: "check" as const, label: "Check", render: (f: Finding) => <span className="num">{f.check.replace(/_/g, " ").toUpperCase()}</span> },
    { key: "ticker" as const, label: "Series", render: (f: Finding) => <span className="num">{f.ticker} · {f.source}</span> },
    { key: "date" as const, label: "Session", render: (f: Finding) => <span className="num">{f.date ? fmtDate(f.date) : "—"}</span> },
    { key: "detail" as const, label: "Detail", render: (f: Finding) => <span>{f.detail}</span> },
  ];
  return (
    <div className="sl-art-body">
      <Readline items={[{ k: "DEFECTS", v: fmtNum(serious.length, 0), tone: serious.length ? "loss" : "" }, { k: "TO LOOK AT", v: fmtNum(rest.length, 0) }, { k: "POLICY", v: "DETECTS · NEVER REPAIRS" }]} />
      {serious.length ? (
        <DataTable<Finding> columns={cols} rows={serious} rowKey={(f) => `${f.source}${f.ticker}${f.date}${f.check}`} compact />
      ) : (
        <Absent reason="NO DEFECTS" source={`${KINDS.integrity} · findings`} />
      )}
      {rest.length > 0 && (
        <>
          <h3 className="sl-sub">LARGE MOVES AND STALE SERIES · POSSIBLY REAL</h3>
          <DataTable<Finding> columns={cols} rows={rest} rowKey={(f) => `${f.source}${f.ticker}${f.date}${f.check}`} compact maxHeight={22 * 10} />
        </>
      )}
    </div>
  );
}
