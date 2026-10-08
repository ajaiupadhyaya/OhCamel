/**
 * MODELS (P5, models.xs_lgbm): EXP-Q01 as pre-registered — a LightGBM ranker against the
 * equal-weight linear composite of the same five features on the frozen ETF universe, the
 * 2022→ holdout evaluated once. The ArtifactCell stamps Lane M's verdict first; then the
 * holdout figures, the charter gates as written by the product, growth of $1 against the
 * composite, costs, regimes, deciles and this month's advisory scores. Nothing here reaches
 * the desk. An unfrozen universe arrives as the product's own INSUFFICIENT DATA verdict with
 * an empty gate table, and no number is shown.
 */
import { ArtifactCell, BarChart, DataTable, Section } from "../../components";
import { XYChart } from "../../charts/XYChart";
import { Absent, Note } from "../../design";
import { KINDS, type Manifest } from "../../lib/artifacts";
import { fmtCurrency, fmtDate, fmtMultiple, fmtNum, fmtPct } from "../../lib/format";
import { GATE_CODE, growthCurve, paramsText, type LabGate } from "./derive";
import { useTable } from "./products";
import { Figs, GateTable, Readline, finite, type Fig } from "./shared";
import type { GateRow, HoldoutRow, RegimeRow, ScoreRow } from "./types";

export interface ModelCostRow {
  cost_bps: number;
  model_sharpe: number | null;
  composite_sharpe: number | null;
  model_total: number | null;
}

export interface DecileRow {
  decile: number;
  mean_next_month_return: number | null;
}

export function ModelsView() {
  return (
    <Section
      title={
        <>
          Models · P5 · EXP-Q01
          <Note n={1} to="p5-exp-q01" />
        </>
      }
    >
      <ArtifactCell title="XS ranker vs linear composite · ETF universe" kind={KINDS.models} experiment="EXP-Q01" span="all">
        {(m) => <ModelsTables m={m} />}
      </ArtifactCell>
    </Section>
  );
}

function ModelsTables({ m }: { m: Manifest }) {
  const has = (t: string) => !m.tables || m.tables.includes(t);
  const evaluated = has("holdout");
  // An unevaluated artifact carries only its (empty) gate table: read nothing else.
  const holdout = useTable<HoldoutRow>(KINDS.models, evaluated ? m : null, "holdout");
  const gates = useTable<GateRow>(KINDS.models, m, "gates");
  const costs = useTable<ModelCostRow>(KINDS.models, evaluated ? m : null, "holdout_costs");
  const regimes = useTable<RegimeRow>(KINDS.models, evaluated ? m : null, "holdout_regimes");
  const deciles = useTable<DecileRow>(KINDS.models, evaluated ? m : null, "holdout_deciles");
  const returns = useTable<Record<string, unknown>>(KINDS.models, evaluated ? m : null, "holdout_returns");
  const scores = useTable<ScoreRow>(KINDS.models, has("scores") ? m : null, "scores");
  if (evaluated && holdout.block) return <>{holdout.block}</>;
  return (
    <ModelsReport
      holdout={holdout.rows?.[0] ?? null}
      gates={gates.rows ?? []}
      costs={costs.rows ?? []}
      regimes={regimes.rows ?? []}
      deciles={deciles.rows ?? []}
      returns={returns.rows ?? []}
      scores={scores.rows ?? []}
      scoredAt={m.data_asof ?? null}
    />
  );
}

/** A gate row as the lab's gate shape: value formatted by gate, status from `passed`. */
export function gateOf(g: GateRow): LabGate {
  const v = g.value;
  const value = !finite(v) ? "—" : g.gate === "holdout_positive" ? fmtPct(v, 1, { signed: true }) : g.gate === "beats_linear_composite" ? fmtNum(v, 2, { signed: true }) : fmtNum(v, Number.isInteger(v) ? 0 : 2);
  return { code: GATE_CODE[g.gate] ?? g.gate.replace(/_/g, " ").toUpperCase(), value, rule: (g.rule ?? "").toUpperCase(), status: g.passed === true ? "PASS" : g.passed === false ? "FAIL" : "REPORTED" };
}

export function ModelsReport({ holdout: h, gates, costs, regimes, deciles, returns, scores, scoredAt = null }: { holdout: HoldoutRow | null; gates: GateRow[]; costs: ModelCostRow[]; regimes: RegimeRow[]; deciles: DecileRow[]; returns: Record<string, unknown>[]; scores: ScoreRow[]; scoredAt?: string | null }) {
  if (!h) return <Absent reason="HOLDOUT NOT EVALUATED" source={`${KINDS.models} · holdout`} />;
  const figs: Fig[] = [
    { k: "NET SR", v: fmtNum(h.net_sharpe, 2), sub: `LIN ${fmtNum(h.composite_net_sharpe, 2)}`, tone: finite(h.net_sharpe) && h.net_sharpe < 0 ? "loss" : "" },
    { k: "RANK IC", v: fmtNum(h.rank_ic_mean, 3), sub: `t ${fmtNum(h.rank_ic_hac_t, 2)} · HAC` },
    { k: "DSR", v: fmtNum(h.dsr, 2), sub: `N ${fmtNum(h.n_trials, 0)}` },
    { k: "PSR", v: fmtNum(h.psr, 2) },
    { k: "MAX DD", v: fmtPct(h.max_drawdown, 1), tone: finite(h.max_drawdown) && h.max_drawdown < 0 ? "loss" : "" },
    { k: "CAPACITY", v: fmtCurrency(h.capacity_usd, { compact: true, digits: 1 }), sub: "MEDIAN · ADV SHARE" },
  ];
  const g = growthCurve(returns);
  return (
    <div className="sl-art-body">
      <Readline
        items={[
          { k: "HOLDOUT", v: `${fmtDate(h.holdout_start)} – ${fmtDate(h.holdout_end)}` },
          { k: "CONFIG", v: `${fmtNum(h.selected_config, 0)} · ${paramsText(h.params)}` },
          { k: "IC", v: `${fmtNum(h.ic_mean, 3)} · t ${fmtNum(h.ic_hac_t, 2)}` },
          { k: "TOTAL", v: fmtPct(h.total_return, 1, { signed: true }), tone: finite(h.total_return) && h.total_return < 0 ? "loss" : "" },
          { k: "TURNOVER", v: `${fmtMultiple(h.annual_turnover, 1)}/Y` },
          { k: "PBO", v: fmtNum(h.pbo, 2) },
          { k: "BOOT LO 5%", v: fmtNum(h.boot_lo5, 2), tone: finite(h.boot_lo5) && h.boot_lo5 <= 0 ? "loss" : "" },
          { k: "METHOD", v: `V${fmtNum(h.methodology_version, 0)}` },
        ]}
      />
      <Figs items={figs} />
      {gates.length ? <GateTable gates={gates.map(gateOf)} /> : <Absent reason="NO GATE ROWS" source={`${KINDS.models} · gates`} />}

      {g.x.length > 1 ? (
        <div>
          <h3 className="sl-sub">GROWTH OF $1 · NET · HOLDOUT</h3>
          <XYChart
            x={g.x}
            time
            series={[
              { name: "MODEL", y: g.model, tone: "ink" },
              { name: "LIN", y: g.composite, tone: "ink2", dash: "dash" },
            ]}
            hlines={[{ at: 1, label: "1", tone: "ink3", dash: "dot" }]}
            yFormat="num"
            digits={2}
            height={240}
            ariaLabel="Growth of one dollar, model against the linear composite, holdout"
          />
        </div>
      ) : (
        <Absent reason="NO HOLDOUT RETURNS" source={`${KINDS.models} · holdout_returns`} />
      )}

      <div className="sl-detail">
        <div>
          <h3 className="sl-sub">COSTS · BP ONE WAY</h3>
          {costs.length ? (
            <DataTable<ModelCostRow>
              columns={[
                { key: "cost_bps", label: "BP", numeric: true, format: (v) => fmtNum(v, 0) },
                { key: "model_sharpe", label: "SR", numeric: true, format: (v) => fmtNum(v, 2), color: "sign" },
                { key: "composite_sharpe", label: "LIN SR", numeric: true, format: (v) => fmtNum(v, 2), color: "sign" },
                { key: "model_total", label: "Total", numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), color: "sign" },
              ]}
              rows={costs}
              rowKey={(c) => c.cost_bps}
              compact
            />
          ) : (
            <Absent reason="NO COST LADDER" source={`${KINDS.models} · holdout_costs`} />
          )}
        </div>
        <div>
          <h3 className="sl-sub">REGIMES · SELECTION + HOLDOUT</h3>
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
            <Absent reason="NO REGIME ROWS" source={`${KINDS.models} · holdout_regimes`} />
          )}
        </div>
        <div>
          <h3 className="sl-sub">DECILES · MEAN NEXT-MONTH RETURN</h3>
          {deciles.length ? (
            <BarChart x={deciles.map((d) => `D${d.decile}`)} y={deciles.map((d) => d.mean_next_month_return)} yFormat="pct" digits={2} colorBySign height={180} />
          ) : (
            <Absent reason="NO DECILES" source={`${KINDS.models} · holdout_deciles`} />
          )}
        </div>
      </div>

      <div>
        <h3 className="sl-sub">SCORES · {scoredAt ? fmtDate(scoredAt) : "—"} · ADVISORY</h3>
        {scores.length ? (
          <DataTable<ScoreRow>
            columns={[
              { key: "rank", label: "#", numeric: true, format: (v) => fmtNum(v, 0) },
              { key: "ticker", label: "Ticker", render: (s) => <span className="num">{s.ticker}</span> },
              { key: "score", label: "Score", numeric: true, format: (v) => fmtNum(v, 3) },
              { key: "side", label: "Side", render: (s) => <span className={`num ${s.side === "none" ? "sl-gate-dim" : ""}`}>{String(s.side).toUpperCase()}</span> },
            ]}
            rows={scores}
            rowKey={(s) => s.ticker}
            compact
            maxHeight={22 * 12}
          />
        ) : (
          <Absent reason="NO SCORES" source={`${KINDS.models} · scores`} />
        )}
      </div>
    </div>
  );
}
