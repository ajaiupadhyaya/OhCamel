/**
 * Recession — GET /api/macro/recession?h=&extra=: Estrella–Mishkin term-spread probit
 * (optionally + NFCI), fitted probability history indexed by TARGET month with NBER
 * recessions shaded, the current h-month-ahead probability, coefficients, and the Sahm rule.
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, InfoTip, Panel, SegmentedControl, Toggle, withAlpha, type Column } from "../../components";
import { fmtDate, fmtNum, fmtPct } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import { INFO } from "./info";
import { Controls, KV, MethodCard, Swatch, recessionShapes } from "./shared";
import type { ProbitModel, RecessionOut } from "./types";

const HORIZONS = ["6", "12", "18", "24"] as const;
const RANGES = [
  { value: "all", label: "All" },
  { value: "1990", label: "1990–" },
  { value: "2005", label: "2005–" },
] as const;

export function RecessionTab() {
  const [h, setH] = useState<string>("12");
  const [nfci, setNfci] = useState(true);
  const [range, setRange] = useState<string>("all");
  const q = useApiQuery<RecessionOut>("/macro/recession", { h: +h, extra: nfci ? "nfci" : "none" });
  const from = range === "all" ? undefined : `${range}-01-01`;

  const controls = (
    <Controls>
      <div className="mc-inline-field">
        <span className="oc-field-label">
          Forecast horizon <InfoTip info={{ text: "How many months ahead the model forecasts. 12 months is the horizon used by Estrella & Mishkin and the New York Fed." }} size={12} />
        </span>
        <SegmentedControl size="sm" options={HORIZONS.map((x) => ({ value: x, label: `${x}m` }))} value={h} onChange={setH} ariaLabel="Forecast horizon (months)" />
      </div>
      <Toggle label="Add financial conditions (NFCI)" checked={nfci} onChange={setNfci} />
      <div className="mc-inline-field">
        <span className="oc-field-label">Chart range</span>
        <SegmentedControl size="sm" options={RANGES.map((r) => ({ value: r.value, label: r.label }))} value={range} onChange={setRange} ariaLabel="Chart range" />
      </div>
    </Controls>
  );
  const method = (
    <MethodCard
      title="An inverted curve as a recession alarm"
      formulas={["P(\\text{REC}_{t+h}=1) = \\Phi(\\alpha + \\beta\\,(y^{10y}_t - y^{3m}_t))"]}
      refs={["Estrella & Mishkin (1998), REStat 80(1)", "Estrella (1998), JBES 16(2) — pseudo-R²", "Newey & West (1987) — HAC errors", "Sahm (2019), Hamilton Project"]}
    >
      When short rates rise above long rates, banks' lending margins shrink and markets are betting the Fed will soon cut. A probit regression turns the 10-year minus 3-month spread into the odds of an NBER recession {h} months later. The Sahm rule is its real-time complement: it fires once unemployment has already started climbing.
    </MethodCard>
  );

  if (q.isError && !q.data) {
    return (
      <div className="stack">
        {controls}
        <div className="grid-3">
          <Panel title="Recession probability" subtitle={`Odds of a U.S. recession ${h} months ahead from the Treasury term spread, with the Sahm rule.`} query={q} span={2} />
          {method}
        </div>
      </div>
    );
  }

  return (
    <div className="stack">
      {controls}
      <div className="grid-3">
        <Panel<RecessionOut> title={`Probability of recession in ${h} months`} subtitle="The model's current reading, from this month's term spread." info={INFO.probit} query={q} skeletonHeight={300} notes={[]} provenance={[]}>
          {(d) => <Current d={d} />}
        </Panel>
        <Panel<RecessionOut>
          title="Probability history"
          subtitle={`Fitted odds of recession, plotted at the month being forecast (${h} months after the spread was observed). Grey bands are NBER-dated recessions: a useful model spikes before or into them.`}
          info={INFO.probit}
          query={q}
          span={2}
          skeletonHeight={300}
          notes={[]}
        >
          {(d) => <ProbHistory d={d} from={from} />}
        </Panel>
      </div>
      <div className="grid-3">
        <Panel<RecessionOut> title="Coefficients" subtitle="Probit estimates with Newey–West (HAC) standard errors. A negative spread coefficient means a flatter or inverted curve raises the odds." info={INFO.probit} query={q} skeletonHeight={260} notes={[]} provenance={[]} flush>
          {(d) => <Coefs d={d} />}
        </Panel>
        <Panel<RecessionOut> title="Term spread (10y − 3m)" subtitle="Monthly average of the model's input. Below zero = inverted." info={INFO.s3m10y} query={q} skeletonHeight={260} notes={[]} provenance={[]}>
          {(d) => <SpreadChart d={d} from={from} />}
        </Panel>
        {method}
      </div>
      <Panel<RecessionOut>
        title="Sahm rule"
        subtitle="How far the 3-month average unemployment rate has risen above its low of the previous year. Crossing 0.50 pp has marked the early months of every recession since 1970 — it confirms, rather than predicts."
        info={INFO.sahm}
        query={q}
        skeletonHeight={300}
      >
        {(d) => (d.sahm ? <SahmChart d={d} from={from} /> : <div className="subtle small">Unemployment data (UNRATE) is unavailable, so the Sahm rule can't be computed — see notes.</div>)}
      </Panel>
    </div>
  );
}

function Current({ d }: { d: RecessionOut }) {
  const p = d.current.probability;
  const p2 = d.current.probability_with_nfci;
  const tone = p >= 0.5 ? "loss" : p >= 0.25 ? "warn" : "";
  const m = d.models.spread;
  return (
    <div className="mc-current">
      <div className={`mc-bignum num ${tone}`}>{fmtPct(p, 0)}</div>
      <div className="subtle small">
        chance the U.S. is in recession in <b>{fmtDate(d.current.target, "month")}</b>, from the {fmtDate(d.current.origin, "month")} spread of <span className="num">{fmtNum(d.current.spread, 2)} pp</span>
      </div>
      <div className="mc-meter" aria-hidden>
        <span style={{ width: `${Math.max(1, p * 100)}%` }} className={tone} />
      </div>
      <KV
        rows={[
          ...(p2 != null ? [{ k: "With NFCI", v: fmtPct(p2, 0), info: INFO.nfci }] : []),
          { k: "Pseudo-R² (Estrella)", v: fmtNum(m.pseudo_r2_estrella, 3), info: INFO.pseudoR2 },
          { k: "Sample", v: `${fmtDate(d.sample.start, "year")}–${fmtDate(d.sample.end, "year")} · ${d.sample.nobs} months`, muted: true },
        ]}
      />
    </div>
  );
}

function ProbHistory({ d, from }: { d: RecessionOut; from?: string }) {
  const P = d.probability;
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const out: Data[] = [{ type: "scatter", mode: "lines", name: "Term spread model", x: P.index, y: P.data.spread_model, line: { color: t.categorical[0], width: 2 }, fill: "tozeroy", fillcolor: withAlpha(t.categorical[0], 0.08), hovertemplate: "<b>Spread</b> %{y:.1%}<extra></extra>" } as Data];
      if (P.data.spread_nfci_model) out.push({ type: "scatter", mode: "lines", name: "Spread + NFCI", x: P.index, y: P.data.spread_nfci_model, line: { color: t.categorical[1], width: 1.5, dash: "dot" }, hovertemplate: "<b>+NFCI</b> %{y:.1%}<extra></extra>" } as Data);
      return out;
    },
    [P],
  );
  const layout = useMemo(
    () => (t: Tokens) =>
      ({
        hovermode: "x unified",
        xaxis: { type: "date", hoverformat: "%b %Y", range: from ? [from, P.index[P.index.length - 1]] : undefined },
        yaxis: { tickformat: ".0%", range: [0, 1], side: "right" },
        shapes: recessionShapes(d.recessions, t),
        margin: { l: 16, r: 8, t: 36, b: 28 },
      }) as any,
    [d.recessions, from, P.index],
  );
  return (
    <>
      <Chart data={data} layout={layout} height={280} ariaLabel="Recession probability history" />
      <div className="mc-legend-row">
        <Swatch color="color-mix(in srgb, var(--text-3) 22%, transparent)" label="NBER recession" />
      </div>
    </>
  );
}

const REG_LABEL: Record<string, string> = { const: "Constant α", spread: "Term spread β", nfci: "NFCI" };

function Coefs({ d }: { d: RecessionOut }) {
  type Row = { id: string; model: string; param: string; coef: number; se: number; p: number };
  const rows: Row[] = [];
  const add = (key: string, name: string, m?: ProbitModel) => {
    if (!m) return;
    for (const k of Object.keys(m.params)) rows.push({ id: `${key}-${k}`, model: name, param: REG_LABEL[k] ?? k, coef: m.params[k], se: m.std_errors[k], p: m.pvalues[k] });
  };
  add("s", "Spread", d.models.spread);
  add("n", "Spread + NFCI", d.models.spread_nfci);
  const cols: Column<Row>[] = [
    { key: "param", label: "Term", render: (r) => <span className="stack" style={{ gap: 0 }}><span>{r.param}</span><span className="subtle small">{r.model}</span></span> },
    { key: "coef", label: "Coef.", numeric: true, format: (v) => fmtNum(v, 2) },
    { key: "se", label: "s.e.", numeric: true, format: (v) => fmtNum(v, 2), hideBelow: 600, info: { text: "Newey–West heteroskedasticity- and autocorrelation-consistent standard error (overlapping forecast horizons make plain errors too small).", reference: "Newey & West (1987), Econometrica 55(3)" } },
    { key: "p", label: "p", numeric: true, format: (v) => (v < 0.001 ? "<0.001" : fmtNum(v, 3)), color: (v) => (v < 0.05 ? "gain" : undefined) },
  ];
  const m = d.models.spread;
  return (
    <>
      <DataTable columns={cols} rows={rows} rowKey={(r) => r.id} />
      <div className="mc-pad">
        <KV
          rows={[
            { k: "McFadden R²", v: fmtNum(m.pseudo_r2_mcfadden, 3), info: INFO.pseudoR2 },
            { k: "Log-likelihood", v: `${fmtNum(m.loglik, 1)} (null ${fmtNum(m.loglik_null, 1)})`, muted: true },
            { k: "Observations", v: m.nobs.toLocaleString(), muted: true },
          ]}
        />
      </div>
    </>
  );
}

function SpreadChart({ d, from }: { d: RecessionOut; from?: string }) {
  const data = useMemo(() => (t: Tokens): Data[] => [{ type: "scatter", mode: "lines", name: "10y − 3m", x: d.spread.index, y: d.spread.values, line: { color: t.categorical[0], width: 1.5 }, hovertemplate: "<b>%{y:.2f} pp</b><extra></extra>" } as Data], [d.spread]);
  const layout = useMemo(
    () => (t: Tokens) => ({ hovermode: "x unified", showlegend: false, xaxis: { type: "date", hoverformat: "%b %Y", range: from ? [from, d.spread.index[d.spread.index.length - 1]] : undefined }, yaxis: { ticksuffix: " pp", side: "right", zeroline: true, zerolinecolor: t.loss }, shapes: recessionShapes(d.recessions, t), margin: { l: 16, r: 8, t: 12, b: 28 } }) as any,
    [d.recessions, d.spread.index, from],
  );
  return <Chart data={data} layout={layout} height={240} ariaLabel="Term spread" />;
}

function SahmChart({ d, from }: { d: RecessionOut; from?: string }) {
  const s = d.sahm!;
  const data = useMemo(
    () => (t: Tokens): Data[] => [
      { type: "scatter", mode: "lines", name: "Sahm indicator", x: s.series.index, y: s.series.data.sahm, line: { color: t.categorical[0], width: 1.8 }, hovertemplate: "<b>Sahm</b> %{y:.2f} pp<extra></extra>" } as Data,
      { type: "scatter", mode: "lines", name: `Trigger (${fmtNum(s.threshold, 2)} pp)`, x: [s.series.index[0], s.series.index[s.series.index.length - 1]], y: [s.threshold, s.threshold], line: { color: t.loss, width: 1, dash: "dash" }, hoverinfo: "skip" } as Data,
    ],
    [s],
  );
  const layout = useMemo(
    () => (t: Tokens) => ({ hovermode: "x unified", xaxis: { type: "date", hoverformat: "%b %Y", range: from ? [from, s.series.index[s.series.index.length - 1]] : undefined }, yaxis: { ticksuffix: " pp", side: "right" }, shapes: recessionShapes(d.recessions, t), margin: { l: 16, r: 8, t: 36, b: 28 } }) as any,
    [d.recessions, s.series.index, from],
  );
  return (
    <>
      <Chart data={data} layout={layout} height={280} ariaLabel="Sahm rule" />
      <div className="mc-legend-row">
        <Swatch color="color-mix(in srgb, var(--text-3) 22%, transparent)" label="NBER recession" />
        <span className="subtle small">
          Latest <span className="num">{fmtNum(s.latest.value, 2)} pp</span> ({fmtDate(s.latest.date, "month")}) — {s.latest.triggered ? <b className="loss">triggered</b> : "not triggered"}
          {s.trigger_dates.length > 0 && <> · first triggers since 1960: {s.trigger_dates.map((x) => fmtDate(x, "year")).join(", ")}</>}
        </span>
      </div>
    </>
  );
}
