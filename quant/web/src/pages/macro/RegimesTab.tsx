/**
 * Regimes — GET /api/macro/regimes?ticker=&k=&freq=: Hamilton (1989) Markov-switching
 * mean/variance model of returns. Price with the most-likely regime shaded behind it,
 * smoothed / filtered probabilities, regime statistics, transition matrix, expected
 * durations and the transparent risk-on/off panel. Works offline on the committed ETFs.
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, HeatmapChart, Panel, SegmentedControl, StatGrid, StatTile, TickerInput, withAlpha, type Column } from "../../components";
import { fmtDate, fmtNum, fmtPct, fmtPctPoints, fmtSignedPct } from "../../lib/format";
import { usePortfolio } from "../../lib/portfolio";
import { useApiQuery } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import { INFO } from "./info";
import { Controls, KV, MethodCard, Swatch, runs } from "./shared";
import type { RegimesOut, RiskComponent } from "./types";

export const FREQ_LABEL = { D: "daily", W: "weekly", M: "monthly" } as const;
const PERIOD_UNIT = { D: "days", W: "weeks", M: "months" } as const;
const PERIOD_ONE = { D: "day", W: "week", M: "month" } as const;

export function regimeNames(k: number): string[] {
  return k === 3 ? ["Calm", "Elevated", "Stress"] : ["Calm", "Turbulent"];
}
/** Regime colours by calm→stress order: gain, (warn), loss. Semantic, not categorical. */
export function regimeColors(t: Tokens | null, k: number): string[] {
  const g = t?.gain ?? "var(--gain)";
  const w = t?.warn ?? "var(--warn)";
  const l = t?.loss ?? "var(--loss)";
  return k === 3 ? [g, w, l] : [g, l];
}

export function useRegimes(ticker: string, k: number, freq: string) {
  return useApiQuery<RegimesOut>("/macro/regimes", { ticker, k, freq }, { placeholderData: undefined });
}

/** Contiguous runs of the most-likely (argmax) regime along the smoothed index. */
export function regimeRuns(d: RegimesOut, which: "smoothed" | "filtered" = "smoothed") {
  const f = d[which];
  const cols = f.columns;
  const idx = f.index;
  const arg = idx.map((_, i) => {
    let best = 0;
    let bv = -1;
    cols.forEach((c, j) => {
      const v = (f.data[c][i] as number | null) ?? -1;
      if (v > bv) {
        bv = v;
        best = j;
      }
    });
    return best;
  });
  return cols.map((_, j) => runs(idx, (i) => arg[i] === j));
}

export function RegimesTab() {
  const { portfolio } = usePortfolio();
  const [ticker, setTicker] = useState("SPY");
  const [k, setK] = useState<"2" | "3">("2");
  const [freq, setFreq] = useState<"D" | "W" | "M">("W");
  const [view, setView] = useState<"smoothed" | "filtered">("smoothed");
  const q = useRegimes(ticker, +k, freq);
  const holdings = useMemo(() => [...new Set(portfolio.holdings.map((h) => h.ticker.toUpperCase()))].slice(0, 8), [portfolio.holdings]);

  return (
    <div className="stack">
      <Controls>
        <div className="mc-inline-field">
          <span className="oc-field-label">Asset</span>
          <div className="row-wrap" style={{ gap: 6 }}>
            <span className="mc-chip on num" aria-live="polite">
              {ticker}
            </span>
            <TickerInput onSelect={(t) => setTicker(t.toUpperCase())} placeholder="Another ticker…" className="mc-ticker-input" />
          </div>
        </div>
        {holdings.length > 0 && (
          <div className="mc-inline-field">
            <span className="oc-field-label" title={portfolio.name}>
              From your portfolio
            </span>
            <div className="mc-chips">
              {holdings.map((t) => (
                <button key={t} type="button" className={`mc-chip num ${t === ticker ? "on" : ""}`} onClick={() => setTicker(t)} aria-pressed={t === ticker}>
                  {t}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="mc-inline-field">
          <span className="oc-field-label">Regimes</span>
          <SegmentedControl size="sm" options={[{ value: "2", label: "2" }, { value: "3", label: "3" }]} value={k} onChange={setK} ariaLabel="Number of regimes" />
        </div>
        <div className="mc-inline-field">
          <span className="oc-field-label">Returns</span>
          <SegmentedControl size="sm" options={[{ value: "D", label: "Daily" }, { value: "W", label: "Weekly" }, { value: "M", label: "Monthly" }]} value={freq} onChange={setFreq} ariaLabel="Return frequency" />
        </div>
      </Controls>

      <Panel<RegimesOut> query={q} skeletonHeight={96} notes={[]} provenance={[]}>
        {(d) => <Headline d={d} />}
      </Panel>

      <div className="grid-3">
        <Panel<RegimesOut>
          title={`${q.data?.ticker ?? ticker} through its regimes`}
          subtitle="Weekly closing price (log scale) with the background coloured by the regime the model thinks was most likely at each date. Calm regimes tend to grind up; turbulent ones cluster around sell-offs."
          info={INFO.markov}
          query={q}
          span={2}
          skeletonHeight={380}
          notes={[]}
        >
          {(d) => <PriceRegimes d={d} />}
        </Panel>
        <MethodCard
          title="Hidden states behind the returns"
          formulas={["r_t = \\mu_{S_t} + \\sigma_{S_t}\\,\\varepsilon_t", "P(S_t=j\\mid S_{t-1}=i) = p_{ij},\\quad E[D_i] = \\tfrac{1}{1-p_{ii}}"]}
          refs={["Hamilton (1989), Econometrica 57(2)", "Kim (1994), J. Econometrics 60 — smoother", "Ang & Bekaert (2002), JBES 20(2)"]}
        >
          Markets alternate between quiet stretches and stressed ones. The model assumes each {PERIOD_ONE[freq]}'s return comes from one of {k} hidden regimes with its own average and volatility, estimates them by maximum likelihood, and infers the probability of each regime at every date. Regimes are ordered by volatility, so regime 0 is always the calmest.
        </MethodCard>
      </div>

      <Panel<RegimesOut>
        title="Regime probabilities"
        subtitle={view === "smoothed" ? "Probability of each regime at each date using the full sample — the clearest picture of history (it uses hindsight)." : "Probability of each regime using only data up to that date — what you would have believed in real time."}
        info={view === "smoothed" ? INFO.smoothed : INFO.filtered}
        query={q}
        skeletonHeight={260}
        notes={[]}
        provenance={[]}
        actions={<SegmentedControl size="sm" options={[{ value: "smoothed", label: "Smoothed" }, { value: "filtered", label: "Filtered (real-time)" }]} value={view} onChange={setView} ariaLabel="Probability type" />}
      >
        {(d) => <Probabilities d={d} which={view} />}
      </Panel>

      <div className="grid-3">
        <Panel<RegimesOut> title="Regime statistics" subtitle="Annualized average return and volatility within each regime, how long it typically lasts and how much of the sample it covers." info={INFO.markov} query={q} skeletonHeight={220} notes={[]} provenance={[]} flush span={2}>
          {(d) => <StatsTable d={d} />}
        </Panel>
        <Panel<RegimesOut> title="Transition matrix" subtitle={`Chance of moving from the row's regime to the column's regime next ${PERIOD_ONE[freq]}.`} info={INFO.transition} query={q} skeletonHeight={220} notes={[]} provenance={[]}>
          {(d) => {
            const names = regimeNames(d.k);
            return <HeatmapChart x={names.map((n) => `→ ${n}`)} y={names.map((n) => `${n} →`)} z={d.model.transition} format="pct" digits={1} showValues zmin={0} zmax={1} diverging={false} colorbar={false} height={60 + d.k * 56} />;
          }}
        </Panel>
      </div>

      <Panel<RegimesOut>
        title="Risk-on / risk-off panel"
        subtitle="A second, model-free read: simple signals each scored from 0 (risk-off) to 1 (risk-on) against their own history, then averaged."
        info={INFO.riskPanel}
        query={q}
        skeletonHeight={160}
      >
        {(d) => <RiskPanel d={d} />}
      </Panel>
    </div>
  );
}

function Headline({ d }: { d: RegimesOut }) {
  const names = regimeNames(d.k);
  const cur = d.model.current_regime;
  const r = d.regimes[cur];
  const unit = PERIOD_UNIT[d.freq];
  return (
    <StatGrid min={150}>
      <StatTile label="Current regime" value={<span className={cur === 0 ? "gain" : cur === d.k - 1 ? "loss" : "warn"}>{names[cur]}</span>} info={INFO.filtered} caption={`${fmtPct(d.model.current_prob, 0)} probability (real-time)`} />
      <StatTile label="Return in this regime" value={r.mean_ann} format={(v) => fmtSignedPct(v, 1)} tone="auto" caption="annualized" />
      <StatTile label="Volatility in this regime" value={r.vol_ann} format={(v) => fmtPct(v, 1)} info="vol" caption="annualized" />
      <StatTile label="Typical length" value={r.expected_duration_periods != null ? `${fmtNum(r.expected_duration_periods, 1)} ${unit}` : null} info={INFO.duration} />
      <StatTile label="Risk composite" value={d.risk_panel.composite} format={(v) => fmtNum(v, 2)} tone={d.risk_panel.state === "risk-on" ? "gain" : d.risk_panel.state === "risk-off" ? "loss" : "neutral"} info={INFO.riskPanel} caption={d.risk_panel.state} />
    </StatGrid>
  );
}

function PriceRegimes({ d }: { d: RegimesOut }) {
  const names = regimeNames(d.k);
  const data = useMemo(() => (t: Tokens): Data[] => [{ type: "scatter", mode: "lines", name: d.ticker, x: d.price.index, y: d.price.values, line: { color: t.text, width: 1.6 }, hovertemplate: "<b>%{y:,.2f}</b><extra></extra>" } as Data], [d]);
  const layout = useMemo(
    () => (t: Tokens) => {
      const cols = regimeColors(t, d.k);
      const shapes = regimeRuns(d).flatMap((rs, j) => rs.map(([a, b]) => ({ type: "rect", xref: "x", yref: "paper", x0: a, x1: b, y0: 0, y1: 1, fillcolor: withAlpha(cols[j], j === 0 ? 0.1 : 0.2), line: { width: 0 }, layer: "below" })));
      return { hovermode: "x unified", showlegend: false, xaxis: { type: "date", hoverformat: "%d %b %Y" }, yaxis: { type: "log", side: "right", tickformat: ",.0f" }, shapes, margin: { l: 16, r: 8, t: 12, b: 28 } } as any;
    },
    [d],
  );
  return (
    <>
      <Chart data={data} layout={layout} height={360} ariaLabel="Price with regime shading" />
      <div className="mc-legend-row">
        {names.map((n, j) => (
          <Swatch key={n} color={`color-mix(in srgb, ${regimeColors(null, d.k)[j]} ${j === 0 ? 18 : 30}%, transparent)`} label={`${n} (regime ${j})`} />
        ))}
        <span className="subtle small">most-likely regime from smoothed probabilities</span>
      </div>
    </>
  );
}

function Probabilities({ d, which }: { d: RegimesOut; which: "smoothed" | "filtered" }) {
  const f = d[which];
  const names = regimeNames(d.k);
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const cols = regimeColors(t, d.k);
      return f.columns.map((c, j) => ({ type: "scatter", mode: "lines", name: names[j], x: f.index, y: f.data[c], stackgroup: "p", line: { width: 0.5, color: cols[j] }, fillcolor: withAlpha(cols[j], j === 0 ? 0.35 : 0.55), hovertemplate: `<b>${names[j]}</b> %{y:.0%}<extra></extra>` })) as Data[];
    },
    [f, d.k, names],
  );
  const layout = useMemo(() => ({ hovermode: "x unified", xaxis: { type: "date", hoverformat: "%d %b %Y" }, yaxis: { tickformat: ".0%", range: [0, 1], side: "right" }, margin: { l: 16, r: 8, t: 36, b: 28 } }) as any, []);
  return <Chart data={data} layout={layout} height={240} ariaLabel="Regime probabilities" />;
}

function StatsTable({ d }: { d: RegimesOut }) {
  const names = regimeNames(d.k);
  const unit = PERIOD_UNIT[d.freq];
  type Row = (typeof d.regimes)[number] & { name: string };
  const rows: Row[] = d.regimes.map((r) => ({ ...r, name: names[r.regime] }));
  const cols: Column<Row>[] = [
    { key: "name", label: "Regime", render: (r) => <span className="row" style={{ gap: 8 }}><span className="mc-dot" style={{ background: regimeColors(null, d.k)[r.regime] }} />{r.name}</span> },
    { key: "mean_ann", label: "Return (ann.)", numeric: true, format: (v) => fmtSignedPct(v, 1), color: "sign" },
    { key: "vol_ann", label: "Vol (ann.)", numeric: true, format: (v) => fmtPct(v, 1), info: "vol" },
    { key: "expected_duration_periods", label: "Expected duration", numeric: true, format: (v) => (v == null ? "—" : `${fmtNum(v, 1)} ${unit}`), info: INFO.duration },
    { key: "share_of_time", label: "Share of time", numeric: true, format: (v) => fmtPct(v, 0), hideBelow: 600 },
  ];
  return (
    <>
      <DataTable columns={cols} rows={rows} rowKey={(r) => r.regime} />
      <div className="mc-pad">
        <KV
          rows={[
            { k: "Observations", v: `${d.model.nobs.toLocaleString()} ${FREQ_LABEL[d.freq]} returns`, muted: true },
            { k: "Log-likelihood · AIC · BIC", v: `${fmtNum(d.model.loglik, 1)} · ${fmtNum(d.model.aic, 1)} · ${fmtNum(d.model.bic, 1)}`, muted: true, info: { text: "Fit statistics; lower AIC/BIC is better when comparing 2 vs 3 regimes on the same data.", reference: "Akaike (1974); Schwarz (1978)" } },
          ]}
        />
      </div>
    </>
  );
}

const COMP_TEXT: Record<string, string> = {
  vix: "Implied volatility of the S&P 500. Scored 1 − its percentile in its own history: a low VIX is risk-on.",
  hy_oas: "High-yield credit spread over Treasuries. Scored 1 − percentile: tight spreads are risk-on.",
  curve_slope: "10y − 2y Treasury spread. 1 if positive, 0 if the curve is inverted.",
  trend: "Price relative to its 200-day average. 1 if above (uptrend), 0 if below.",
};

function RiskPanel({ d }: { d: RegimesOut }) {
  const fmtVal = (c: RiskComponent) => (c.name === "trend" ? fmtSignedPct(c.value, 1) : c.name === "curve_slope" ? `${fmtNum(c.value, 2)} pp` : c.name === "hy_oas" ? fmtPctPoints(c.value) : fmtNum(c.value, 2));
  return (
    <div className="mc-risk">
      {d.risk_panel.components.map((c) => (
        <div key={c.name} className="mc-risk-item" title={c.rule}>
          <div className="mc-risk-head">
            <span className="mc-risk-label">{c.label}</span>
            <span className="num mc-risk-val">{fmtVal(c)}</span>
          </div>
          <div className="mc-risk-bar" aria-label={`score ${fmtNum(c.score, 2)}`}>
            <span style={{ width: `${Math.max(2, c.score * 100)}%` }} className={c.score > 0.5 ? "on" : c.score < 0.5 ? "off" : ""} />
          </div>
          <div className="mc-risk-foot subtle">
            score <span className="num">{fmtNum(c.score, 2)}</span>
            {c.percentile != null && (
              <>
                {" "}
                · <span className="num">{fmtPct(c.percentile, 0)}</span> pct
              </>
            )}{" "}
            · {fmtDate(c.as_of, "short")}
          </div>
          <div className="mc-risk-text subtle small">{COMP_TEXT[c.name] ?? c.rule}</div>
        </div>
      ))}
      <div className="mc-risk-item mc-risk-total">
        <div className="mc-risk-head">
          <span className="mc-risk-label">Composite</span>
          <span className={`num mc-risk-val ${d.risk_panel.state === "risk-on" ? "gain" : d.risk_panel.state === "risk-off" ? "loss" : ""}`}>{fmtNum(d.risk_panel.composite, 2)}</span>
        </div>
        <div className="mc-risk-bar">
          <span style={{ width: `${Math.max(2, d.risk_panel.composite * 100)}%` }} className={d.risk_panel.composite > 0.5 ? "on" : "off"} />
        </div>
        <div className="mc-risk-foot">
          <span className={`badge ${d.risk_panel.state === "risk-on" ? "gain" : d.risk_panel.state === "risk-off" ? "loss" : ""}`}>{d.risk_panel.state}</span>
        </div>
        <div className="mc-risk-text subtle small">Equal-weighted mean of the {d.risk_panel.components.length} signals available.</div>
      </div>
    </div>
  );
}
