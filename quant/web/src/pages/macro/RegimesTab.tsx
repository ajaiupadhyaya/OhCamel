/**
 * REGIMES — two reads of market state.
 *
 *  P6 · EXP-Q02 (regime.hmm, GET /api/artifacts/regime.hmm/latest + tables): a Gaussian HMM on
 *  weekly SPY return / vol, the 10y–2y slope and the change in HY OAS, refit on schedule; the
 *  filtered (real-time) P(high-vol) beside the smoothed history (hindsight, display only), the
 *  pre-registered holdout test against EWMA and its gates. Verdict first; before Lane M's first
 *  run the cell reads INSUFFICIENT DATA · NOT YET RUN.
 *
 *  MARKOV · DESCRIPTIVE (GET /api/macro/regimes?ticker=&k=&freq=): Hamilton (1989) switching
 *  mean / variance on one asset's returns, computed on request — price with the non-calm states
 *  shaded, filtered / smoothed probabilities, regime statistics, transitions, and the risk panel.
 */
import { useMemo, useState } from "react";
import { ArtifactCell, DataTable, Panel, Section, SegmentedControl, StatGrid, StatTile, TickerInput, type Column } from "../../components";
import { XYChart } from "../../charts/XYChart";
import { Absent, Note } from "../../design";
import { KINDS, type Manifest } from "../../lib/artifacts";
import { fmtDate, fmtNum, fmtPct, fmtPctPoints, fmtSignedPct } from "../../lib/format";
import { usePortfolio } from "../../lib/portfolio";
import { useApiQuery } from "../../lib/query";
import { useTable } from "../research/products";
import { Q02_GATE, argmaxStates, latestProb, probRows, q02Holdout, regimeBands, type ProbRow, type Q02Holdout } from "./derive";
import { INFO } from "./info";
import { Controls, Ctl, KV, Readline, Sub } from "./shared";
import type { RegimesOut, RiskComponent } from "./types";

const PERIOD_UNIT = { D: "D", W: "W", M: "M" } as const;

export function regimeNames(k: number): string[] {
  return k === 3 ? ["CALM", "ELEVATED", "STRESS"] : ["CALM", "TURBULENT"];
}

export function useRegimes(ticker: string, k: number, freq: string) {
  return useApiQuery<RegimesOut>("/macro/regimes", { ticker, k, freq }, { placeholderData: undefined });
}

export function RegimesTab() {
  return (
    <div className="stack">
      <Section
        title={
          <>
            Regimes · P6 · EXP-Q02
            <Note n={1} to="p6-exp-q02" />
          </>
        }
      >
        <ArtifactCell title="HMM · P(HIGH VOL) · FILTERED VS EWMA" kind={KINDS.regimes} experiment="EXP-Q02" span="all">
          {(m) => <Q02Tables m={m} />}
        </ArtifactCell>
      </Section>
      <Markov />
    </div>
  );
}

// ------------------------------------------------------------------ P6 · regime.hmm
export interface GateRowQ02 {
  gate: string;
  value: number | null;
  rule: string | null;
  passed: boolean | null;
}
export interface FitRow {
  refit: string | null;
  k: number | null;
  loglik: number | null;
  bic: number | null;
  weeks: number | null;
  converged: boolean | null;
}

function Q02Tables({ m }: { m: Manifest }) {
  const has = (t: string) => !m.tables || m.tables.includes(t);
  const probs = useTable<Record<string, unknown>>(KINDS.regimes, has("probs") ? m : null, "probs");
  const fits = useTable<Record<string, unknown>>(KINDS.regimes, has("fits") ? m : null, "fits");
  const holdout = useTable<Record<string, unknown>>(KINDS.regimes, has("holdout") ? m : null, "holdout");
  const gates = useTable<Record<string, unknown>>(KINDS.regimes, m, "gates");
  if (probs.block && has("probs")) return <>{probs.block}</>;
  return <Q02Report probs={probRows(probs.rows ?? [])} fits={(fits.rows ?? []).map(fitOf)} holdout={q02Holdout(holdout.rows)} gates={(gates.rows ?? []).map(gateOf)} evaluated={has("holdout")} />;
}

const bool = (x: unknown) => (typeof x === "boolean" ? x : null);
const n = (x: unknown) => (typeof x === "number" && Number.isFinite(x) ? x : null);

export function gateOf(r: Record<string, unknown>): GateRowQ02 {
  return { gate: String(r.gate ?? ""), value: n(r.value), rule: typeof r.rule === "string" ? r.rule : null, passed: bool(r.passed) };
}
export function fitOf(r: Record<string, unknown>): FitRow {
  return { refit: typeof r.refit === "string" ? r.refit.slice(0, 10) : typeof r.refit === "number" ? new Date(r.refit).toISOString().slice(0, 10) : null, k: n(r.k), loglik: n(r.loglik), bic: n(r.bic), weeks: n(r.weeks), converged: bool(r.converged) };
}

const fmtP = (p: number | null) => (p === null ? "—" : p < 0.001 ? "<0.001" : fmtNum(p, 3));

export function Q02Report({ probs, fits, holdout: h, gates, evaluated }: { probs: ProbRow[]; fits: FitRow[]; holdout: Q02Holdout | null; gates: GateRowQ02[]; evaluated: boolean }) {
  const last = latestProb(probs);
  const lastSmoothed = [...probs].reverse().find((r) => r.p_high_smoothed_history !== null) ?? null;
  const lastFit = fits.length ? fits[fits.length - 1] : null;
  return (
    <div className="mc-art">
      {h ? (
        <Readline
          items={[
            { k: "HOLDOUT", v: `${fmtDate(h.holdout_start).toUpperCase()} – ${fmtDate(h.holdout_end).toUpperCase()}` },
            { k: "WEEKS", v: `${fmtNum(h.selection_weeks, 0)} SEL · ${fmtNum(h.holdout_weeks, 0)} HOLD · ${fmtNum(h.boundary_weeks_purged, 0)} PURGED` },
            { k: "HAC LAGS", v: fmtNum(h.hac_lags, 0) },
            h.methodology_version !== null && { k: "METHOD", v: `V${fmtNum(h.methodology_version, 0)}` },
          ]}
        />
      ) : (
        <Absent reason={evaluated ? "HOLDOUT ROW EMPTY" : "HOLDOUT NOT EVALUATED"} source={`${KINDS.regimes} · holdout`} />
      )}
      <StatGrid min={140}>
        <StatTile size="sm" label="P(HIGH VOL)" info={INFO.filtered} value={last ? fmtPct(last.p_high, 0) : null} caption={last ? `FILTERED · ${fmtDate(last.date, "short-year").toUpperCase()}` : "NO FILTERED WEEK"} />
        <StatTile size="sm" label="SMOOTHED" info={INFO.smoothed} value={lastSmoothed ? fmtPct(lastSmoothed.p_high_smoothed_history, 0) : null} caption="HINDSIGHT · DISPLAY ONLY" />
        <StatTile size="sm" label="K · LAST REFIT" value={lastFit ? fmtNum(lastFit.k, 0) : null} caption={lastFit?.refit ? `${fmtDate(lastFit.refit, "short-year").toUpperCase()} · BIC ${fmtNum(lastFit.bic, 1)}` : undefined} />
        {h && <StatTile size="sm" label="HAC t ON P" value={fmtNum(h.hac_t_p, 2)} caption="SELECTION · NEEDS > 2" />}
        {h && <StatTile size="sm" label="DM p" value={fmtP(h.dm_pvalue)} caption={`t ${fmtNum(h.dm_stat, 2, { signed: true })} · ONE-SIDED`} />}
        {h && <StatTile size="sm" label="QLIKE" value={fmtNum(h.qlike_ewma_p, 4)} caption={`EWMA ${fmtNum(h.qlike_ewma, 4)}`} />}
        {h && <StatTile size="sm" label="R² OOS" value={fmtNum(h.oos_r2_ewma_p, 3)} caption={`EWMA ${fmtNum(h.oos_r2_ewma, 3)}`} />}
      </StatGrid>
      {gates.length ? <GateTable gates={gates} /> : <Absent reason="NO GATE ROWS" source={`${KINDS.regimes} · gates`} />}
      {probs.length > 1 ? (
        <div>
          <Sub>P(HIGH VOL) · WEEKLY · FILTERED VS SMOOTHED</Sub>
          <XYChart
            x={probs.map((r) => r.date)}
            time
            series={[
              { name: "FILTERED", y: probs.map((r) => r.p_high), tone: "ink" },
              { name: "SMOOTHED", y: probs.map((r) => r.p_high_smoothed_history), tone: "ink3", dash: "dash" },
            ]}
            hlines={[{ at: 0.5, label: "50%", tone: "ink3", dash: "dot" }]}
            zero
            yFormat="pct"
            digits={0}
            height={260}
            ariaLabel="Filtered and smoothed probability of the high-volatility state, weekly"
          />
        </div>
      ) : (
        <Absent reason="NO PROBABILITY ROWS" source={`${KINDS.regimes} · probs`} />
      )}
      {fits.length > 0 && (
        <div>
          <Sub>REFITS · {fmtNum(fits.length, 0)}</Sub>
          <FitTable fits={[...fits].reverse()} />
        </div>
      )}
    </div>
  );
}

function GateTable({ gates }: { gates: GateRowQ02[] }) {
  const status = (g: GateRowQ02) => (g.passed === true ? "PASS" : g.passed === false ? "FAIL" : "REPORTED");
  const cols: Column<GateRowQ02>[] = [
    { key: "gate", label: "Gate", sortable: false, render: (g) => <span className="num">{Q02_GATE[g.gate] ?? g.gate.replace(/_/g, " ").toUpperCase()}</span> },
    { key: "value", label: "Value", numeric: true, sortable: false, format: (v) => fmtNum(v, 3) },
    { key: "rule", label: "Rule", numeric: true, sortable: false, hideBelow: 600, render: (g) => <span className="mc-dim">{(g.rule ?? "").toUpperCase()}</span> },
    { key: "passed", label: "Status", numeric: true, sortable: false, render: (g) => <span className={g.passed === false ? "loss" : ""}>{status(g)}</span> },
  ];
  return <DataTable<GateRowQ02> columns={cols} rows={gates} rowKey={(g) => g.gate} compact />;
}

function FitTable({ fits }: { fits: FitRow[] }) {
  const cols: Column<FitRow>[] = [
    { key: "refit", label: "Refit", render: (r) => <span className="num">{r.refit ? fmtDate(r.refit, "short-year").toUpperCase() : "—"}</span> },
    { key: "k", label: "K", numeric: true, format: (v) => fmtNum(v, 0) },
    { key: "weeks", label: "Weeks", numeric: true, format: (v) => fmtNum(v, 0) },
    { key: "bic", label: "BIC", numeric: true, format: (v) => fmtNum(v, 1) },
    { key: "loglik", label: "LogLik", numeric: true, hideBelow: 600, format: (v) => fmtNum(v, 1) },
    { key: "converged", label: "EM", numeric: true, render: (r) => <span className={r.converged === false ? "loss" : ""}>{r.converged === null ? "—" : r.converged ? "CONVERGED" : "NOT CONVERGED"}</span> },
  ];
  return <DataTable<FitRow> columns={cols} rows={fits} rowKey={(r, i) => `${r.refit}-${i}`} compact maxHeight={22 * 10} />;
}

// ------------------------------------------------------------------ Markov · descriptive
function Markov() {
  const { portfolio } = usePortfolio();
  const [ticker, setTicker] = useState("SPY");
  const [k, setK] = useState<"2" | "3">("2");
  const [freq, setFreq] = useState<"D" | "W" | "M">("W");
  const [view, setView] = useState<"filtered" | "smoothed">("filtered");
  const q = useRegimes(ticker, +k, freq);
  const holdings = useMemo(() => [...new Set(portfolio.holdings.map((h) => h.ticker.toUpperCase()))].slice(0, 8), [portfolio.holdings]);
  const asOf = q.data?.price.index.length ? String(q.data.price.index[q.data.price.index.length - 1]) : undefined;

  return (
    <Section
      title={
        <>
          Markov · returns · descriptive
          <Note n={2} to="regimes" />
        </>
      }
    >
      <Controls>
        <Ctl label="ASSET">
          <span className="mc-chip num on" aria-live="polite">
            {ticker}
          </span>
          <TickerInput onSelect={(t) => setTicker(t.toUpperCase())} placeholder="TICKER" className="mc-ticker-input" />
        </Ctl>
        {holdings.length > 0 && (
          <Ctl label="BOOK">
            <div className="mc-chips">
              {holdings.map((t) => (
                <button key={t} type="button" className={`mc-chip num ${t === ticker ? "on" : ""}`} onClick={() => setTicker(t)} aria-pressed={t === ticker}>
                  {t}
                </button>
              ))}
            </div>
          </Ctl>
        )}
        <Ctl label="K">
          <SegmentedControl size="sm" options={[{ value: "2", label: "2" }, { value: "3", label: "3" }]} value={k} onChange={setK} ariaLabel="Number of regimes" />
        </Ctl>
        <Ctl label="RETURNS">
          <SegmentedControl size="sm" options={[{ value: "D", label: "D" }, { value: "W", label: "W" }, { value: "M", label: "M" }]} value={freq} onChange={setFreq} ariaLabel="Return frequency" />
        </Ctl>
      </Controls>

      <Panel<RegimesOut> query={q} skeletonHeight={96} notes={[]} provenance={[]} asOf={asOf}>
        {(d) => <Headline d={d} />}
      </Panel>
      <Panel<RegimesOut> title={`${q.data?.ticker ?? ticker} · LOG PRICE`} query={q} skeletonHeight={320} notes={[]} asOf={asOf}>
        {(d) => <PriceRegimes d={d} />}
      </Panel>
      <Panel<RegimesOut>
        title={`P(STATE) · ${view === "filtered" ? "FILTERED" : "SMOOTHED · HINDSIGHT"}`}
        query={q}
        skeletonHeight={240}
        notes={[]}
        provenance={[]}
        asOf={asOf}
        actions={<SegmentedControl size="sm" options={[{ value: "filtered", label: "FILTERED" }, { value: "smoothed", label: "SMOOTHED" }]} value={view} onChange={setView} ariaLabel="Probability type" />}
      >
        {(d) => <Probabilities d={d} which={view} />}
      </Panel>
      <div className="grid-3">
        <Panel<RegimesOut> title="STATES" query={q} skeletonHeight={200} notes={[]} provenance={[]} flush span={2}>
          {(d) => <StatsTable d={d} />}
        </Panel>
        <Panel<RegimesOut> title="TRANSITION · ROW → COL" query={q} skeletonHeight={200} notes={[]} provenance={[]} flush>
          {(d) => <Transition d={d} />}
        </Panel>
      </div>
      <Panel<RegimesOut> title="RISK PANEL · 0 OFF · 1 ON" query={q} skeletonHeight={160} flush>
        {(d) => <RiskPanel d={d} />}
      </Panel>
    </Section>
  );
}

function Headline({ d }: { d: RegimesOut }) {
  const names = regimeNames(d.k);
  const cur = d.model.current_regime;
  const r = d.regimes[cur];
  const unit = PERIOD_UNIT[d.freq];
  return (
    <StatGrid min={140}>
      <StatTile size="sm" label="STATE · NOW" info={INFO.filtered} value={names[cur]} caption={`${fmtPct(d.model.current_prob, 0)} FILTERED`} />
      <StatTile size="sm" label="μ · STATE" value={r.mean_ann} format={(v) => fmtSignedPct(v, 1)} tone="auto" caption="ANN" />
      <StatTile size="sm" label="σ · STATE" info="vol" value={r.vol_ann} format={(v) => fmtPct(v, 1)} caption="ANN" />
      <StatTile size="sm" label="E[DURATION]" info={INFO.duration} value={r.expected_duration_periods != null ? `${fmtNum(r.expected_duration_periods, 1)} ${unit}` : null} />
      <StatTile size="sm" label="RISK COMPOSITE" info={INFO.riskPanel} value={d.risk_panel.composite} format={(v) => fmtNum(v, 2)} caption={d.risk_panel.state.toUpperCase()} />
    </StatGrid>
  );
}

function PriceRegimes({ d }: { d: RegimesOut }) {
  const bands = useMemo(() => {
    const f = d.smoothed;
    const states = argmaxStates(f.columns, f.data as Record<string, (number | null)[]>, f.index.length);
    return regimeBands(f.index, states, d.k);
  }, [d]);
  return (
    <>
      <Readline items={[{ k: "STATE", v: "ARGMAX SMOOTHED" }, ...regimeNames(d.k).slice(1).map((nm, j) => ({ k: nm, v: j === d.k - 2 ? "HATCHED" : "SHADED" }))]} />
      <XYChart x={d.price.index} time series={[{ name: d.ticker, y: d.price.values as (number | null)[], tone: "ink" }]} logY bands={bands} yFormat="num" digits={2} height={300} ariaLabel={`${d.ticker} price with regime spans shaded`} />
    </>
  );
}

function Probabilities({ d, which }: { d: RegimesOut; which: "smoothed" | "filtered" }) {
  const f = d[which];
  const names = regimeNames(d.k);
  const series = f.columns.slice(1).map((c, j) => ({ name: names[j + 1], y: f.data[c] as (number | null)[], tone: (j === f.columns.length - 2 ? "ink" : "ink3") as "ink" | "ink3" }));
  return <XYChart x={f.index} time series={series} hlines={[{ at: 0.5, label: "50%", tone: "ink3", dash: "dot" }]} zero yFormat="pct" digits={0} height={220} ariaLabel={`${which} regime probabilities`} />;
}

function StatsTable({ d }: { d: RegimesOut }) {
  const names = regimeNames(d.k);
  const unit = PERIOD_UNIT[d.freq];
  type Row = (typeof d.regimes)[number] & { name: string };
  const rows: Row[] = d.regimes.map((r) => ({ ...r, name: names[r.regime] }));
  const cols: Column<Row>[] = [
    { key: "name", label: "State", render: (r) => <span className="num">{r.name}</span> },
    { key: "mean_ann", label: "μ ann", numeric: true, format: (v) => fmtSignedPct(v, 1), color: "sign" },
    { key: "vol_ann", label: "σ ann", numeric: true, format: (v) => fmtPct(v, 1), info: "vol" },
    { key: "expected_duration_periods", label: "E[dur]", numeric: true, format: (v) => (v == null ? "—" : `${fmtNum(v, 1)} ${unit}`), info: INFO.duration },
    { key: "share_of_time", label: "Share", numeric: true, format: (v) => fmtPct(v, 0), hideBelow: 600 },
  ];
  return (
    <>
      <DataTable<Row> columns={cols} rows={rows} rowKey={(r) => r.regime} compact />
      <div className="mc-pad">
        <KV
          cols={2}
          rows={[
            { k: "N", v: `${fmtNum(d.model.nobs, 0)} ${unit}` },
            { k: "LOGLIK", v: fmtNum(d.model.loglik, 1) },
            { k: "AIC", v: fmtNum(d.model.aic, 1) },
            { k: "BIC", v: fmtNum(d.model.bic, 1) },
          ]}
        />
      </div>
    </>
  );
}

function Transition({ d }: { d: RegimesOut }) {
  const names = regimeNames(d.k);
  type Row = { from: string; p: number[] };
  const rows: Row[] = d.model.transition.map((p, i) => ({ from: names[i], p }));
  const cols: Column<Row>[] = [{ key: "from", label: "From", render: (r) => <span className="num">{r.from}</span> }, ...names.map((nm, j) => ({ key: `p${j}`, label: nm, numeric: true, value: (r: Row) => r.p[j], format: (v: number) => fmtPct(v, 1) }))];
  return <DataTable<Row> columns={cols} rows={rows} rowKey={(r) => r.from} compact />;
}

function RiskPanel({ d }: { d: RegimesOut }) {
  const fmtVal = (c: RiskComponent) => (c.name === "trend" ? fmtSignedPct(c.value, 1) : c.name === "curve_slope" ? `${fmtNum(c.value, 2, { signed: true })} PP` : c.name === "hy_oas" ? fmtPctPoints(c.value) : fmtNum(c.value, 2));
  type Row = RiskComponent | { name: "composite"; label: string; value: number; score: number; percentile: null; as_of: string; rule: string };
  const rows: Row[] = [...d.risk_panel.components, { name: "composite", label: `COMPOSITE · ${d.risk_panel.state.toUpperCase()}`, value: d.risk_panel.composite, score: d.risk_panel.composite, percentile: null, as_of: "", rule: `MEAN OF ${d.risk_panel.components.length}` }];
  const cols: Column<Row>[] = [
    { key: "label", label: "Signal", render: (r) => <span className={r.name === "composite" ? "mc-strong" : ""}>{r.label.toUpperCase()}</span> },
    { key: "value", label: "Value", numeric: true, render: (r) => <span>{r.name === "composite" ? "—" : fmtVal(r as RiskComponent)}</span> },
    { key: "percentile", label: "Pctile", numeric: true, hideBelow: 600, format: (v) => fmtPct(v, 0) },
    { key: "score", label: "Score", numeric: true, render: (r) => <span className={r.name === "composite" ? "mc-strong" : ""}>{fmtNum(r.score, 2)}</span>, info: INFO.riskPanel },
    { key: "rule", label: "Rule", hideBelow: 900, render: (r) => <span className="mc-dim">{r.rule}</span> },
    { key: "as_of", label: "As of", numeric: true, hideBelow: 600, render: (r) => <span>{r.as_of ? fmtDate(r.as_of, "short-year").toUpperCase() : ""}</span> },
  ];
  return <DataTable<Row> columns={cols} rows={rows} rowKey={(r) => r.name} compact />;
}
