/**
 * Policy — GET /api/macro/taylor?r_star=&pi_star=&start=: Taylor (1993) and balanced-approach
 * prescriptions vs the effective fed funds rate. r* and π* are RULE PARAMETERS the user sets
 * (defaults = Taylor's 1993 calibration), never presented as data.
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Callout, Chart, NumberField, Panel, SegmentedControl, StatGrid, StatTile } from "../../components";
import { fmtDate, fmtNum, fmtPctPoints } from "../../lib/format";
import { useDebounced } from "../../lib/hooks";
import { useApiQuery } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import { INFO } from "./info";
import { Controls, MethodCard } from "./shared";
import type { TaylorOut } from "./types";

const STARTS = [
  { value: "1990", label: "1990–" },
  { value: "2000", label: "2000–" },
  { value: "2010", label: "2010–" },
  { value: "all", label: "All" },
] as const;

export function PolicyTab() {
  const [rStar, setRStar] = useState(2);
  const [piStar, setPiStar] = useState(2);
  const [start, setStart] = useState<string>("1990");
  const params = useDebounced({ r_star: rStar, pi_star: piStar, start: start === "all" ? undefined : `${start}-01-01` }, 250);
  const q = useApiQuery<TaylorOut>("/macro/taylor", params);
  const isDefault = rStar === 2 && piStar === 2;

  const controls = (
    <Controls
      right={
        !isDefault ? (
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => (setRStar(2), setPiStar(2))}>
            Reset to Taylor (1993): 2 / 2
          </button>
        ) : (
          <span className="subtle small">Taylor's 1993 calibration</span>
        )
      }
    >
      <div className="mc-param-box">
        <span className="mc-param-tag">Rule parameters — your assumptions, not data</span>
        <div className="row-wrap" style={{ gap: 16 }}>
          <NumberField label="r* neutral real rate" value={rStar} onChange={setRStar} unit="%" min={-5} max={10} step={0.25} width={110} info={INFO.rstar} />
          <NumberField label="π* inflation target" value={piStar} onChange={setPiStar} unit="%" min={0} max={10} step={0.25} width={110} info={INFO.pistar} />
        </div>
      </div>
      <div className="mc-inline-field">
        <span className="oc-field-label">From</span>
        <SegmentedControl size="sm" options={STARTS.map((s) => ({ value: s.value, label: s.label }))} value={start} onChange={setStart} ariaLabel="Start year" />
      </div>
    </Controls>
  );
  const method = (
    <MethodCard
      title="Where 'should' the policy rate be?"
      formulas={["i^{\\text{Taylor}} = r^* + \\pi + 0.5(\\pi - \\pi^*) + 0.5\\,\\text{gap}", "i^{\\text{BA}} = r^* + \\pi + 0.5(\\pi - \\pi^*) + 1.0\\,\\text{gap}"]}
      refs={["Taylor (1993), Carnegie-Rochester Conf. Series 39", "Yellen (2012), Boston Economic Club speech", "Fed Monetary Policy Report — policy-rules box"]}
    >
      A policy rule turns two observable gaps — inflation vs target (core PCE, year over year) and output vs potential (real GDP vs CBO potential) — into a benchmark fed funds rate. It is a yardstick, not a forecast: the Fed deviates on purpose (e.g. at the zero lower bound), and the answer depends heavily on the r* you assume.
    </MethodCard>
  );

  if (q.isError && !q.data) {
    return (
      <div className="stack">
        {controls}
        <div className="grid-3">
          <Panel title="Taylor rule vs the fed funds rate" subtitle="Rule-implied policy rates from core PCE inflation and the CBO output gap, against the actual effective fed funds rate." query={q} span={2} />
          {method}
        </div>
      </div>
    );
  }

  return (
    <div className="stack">
      {controls}
      <Panel<TaylorOut> query={q} skeletonHeight={96} notes={[]} provenance={[]}>
        {(d) => {
          const l = d.latest;
          return (
            <StatGrid min={150}>
              <StatTile label="Fed funds (effective)" value={fmtPctPoints(l.fed_funds)} caption={`${fmtDate(l.date, "month")} average`} />
              <StatTile label="Taylor (1993)" value={fmtPctPoints(l.taylor_1993)} info={INFO.taylor} caption={l.gap_taylor_minus_ff != null ? `${fmtNum(l.gap_taylor_minus_ff, 2, { signed: true })} pp vs actual` : undefined} />
              <StatTile label="Balanced approach" value={fmtPctPoints(l.balanced_approach)} info={INFO.balanced} caption={l.gap_balanced_minus_ff != null ? `${fmtNum(l.gap_balanced_minus_ff, 2, { signed: true })} pp vs actual` : undefined} />
              <StatTile label="Core PCE inflation" value={fmtPctPoints(l.inflation)} info={INFO.yoy} caption={`target π* = ${fmtNum(d.params.pi_star, 2)}%`} />
              <StatTile label="Output gap" value={`${fmtNum(l.output_gap, 2, { signed: true })}%`} info={INFO.gap} caption="of potential GDP" />
            </StatGrid>
          );
        }}
      </Panel>
      <div className="grid-3">
        <Panel<TaylorOut>
          title="Rule prescriptions vs actual policy"
          subtitle={`Where two standard rules would put the policy rate given inflation and the output gap (with r* = ${fmtNum(rStar, 2)}%, π* = ${fmtNum(piStar, 2)}%), against the rate the Fed actually set. A rule above the actual line means policy was looser than the rule suggests.`}
          info={INFO.taylor}
          query={q}
          span={2}
          skeletonHeight={360}
          notes={[]}
        >
          {(d) => <RulesChart d={d} />}
        </Panel>
        {method}
      </div>
      <div className="grid-2">
        <Panel<TaylorOut> title="Policy gap" subtitle="Rule minus actual fed funds, in percentage points. Positive = policy easier than the rule; negative = tighter." info={{ text: "Prescribed rate minus the monthly average effective fed funds rate (DFF)." }} query={q} skeletonHeight={260} notes={[]} provenance={[]}>
          {(d) => <GapChart d={d} />}
        </Panel>
        <Panel<TaylorOut> title="The rule's inputs" subtitle="Core PCE inflation (y/y) and the output gap — the two things the rule reacts to." info={INFO.gap} query={q} skeletonHeight={260}>
          {(d) => <InputsChart d={d} />}
        </Panel>
      </div>
      <Callout tone="info" title="About r* and π*">
        They are inputs to the rule, not measurements. Taylor used 2% and 2%; the Fed's target is 2% PCE inflation, while estimates of the neutral real rate have ranged from about 0.5% to 2% since 2010 (Holston–Laubach–Williams). No r* estimate is published on FRED, so no data-driven r* is shown.
      </Callout>
    </div>
  );
}

function RulesChart({ d }: { d: TaylorOut }) {
  const s = d.series;
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const out: Data[] = [
        { type: "scatter", mode: "lines", name: "Taylor (1993)", x: s.index, y: s.data.taylor_1993, line: { color: t.categorical[0], width: 1.8 }, hovertemplate: "<b>Taylor</b> %{y:.2f}%<extra></extra>" } as Data,
        { type: "scatter", mode: "lines", name: "Balanced approach", x: s.index, y: s.data.balanced_approach, line: { color: t.categorical[1], width: 1.6, dash: "dot" }, hovertemplate: "<b>Balanced</b> %{y:.2f}%<extra></extra>" } as Data,
      ];
      if (s.data.fed_funds) out.push({ type: "scatter", mode: "lines", name: "Effective fed funds (actual)", x: s.index, y: s.data.fed_funds, line: { color: t.text, width: 2.2 }, hovertemplate: "<b>Actual</b> %{y:.2f}%<extra></extra>" } as Data);
      return out;
    },
    [s],
  );
  const layout = useMemo(() => (t: Tokens) => ({ hovermode: "x unified", xaxis: { type: "date", hoverformat: "%b %Y" }, yaxis: { ticksuffix: "%", side: "right", zeroline: true, zerolinecolor: t.ruleStrong }, margin: { l: 16, r: 8, t: 36, b: 28 } }) as any, []);
  return <Chart data={data} layout={layout} height={340} ariaLabel="Taylor rule vs fed funds" />;
}

function GapChart({ d }: { d: TaylorOut }) {
  const s = d.series;
  const data = useMemo(
    () => (t: Tokens): Data[] =>
      [
        s.data.gap_taylor_minus_ff && { type: "scatter", mode: "lines", name: "Taylor − actual", x: s.index, y: s.data.gap_taylor_minus_ff, line: { color: t.categorical[0], width: 1.6 }, hovertemplate: "<b>Taylor − FF</b> %{y:+.2f} pp<extra></extra>" },
        s.data.gap_balanced_minus_ff && { type: "scatter", mode: "lines", name: "Balanced − actual", x: s.index, y: s.data.gap_balanced_minus_ff, line: { color: t.categorical[1], width: 1.4, dash: "dot" }, hovertemplate: "<b>Balanced − FF</b> %{y:+.2f} pp<extra></extra>" },
      ].filter(Boolean) as Data[],
    [s],
  );
  const layout = useMemo(() => (t: Tokens) => ({ hovermode: "x unified", xaxis: { type: "date", hoverformat: "%b %Y" }, yaxis: { ticksuffix: " pp", side: "right", zeroline: true, zerolinecolor: t.text3 }, margin: { l: 16, r: 8, t: 36, b: 28 } }) as any, []);
  if (!s.data.gap_taylor_minus_ff) return <div className="subtle small">The effective fed funds rate is unavailable, so the gap can't be computed.</div>;
  return <Chart data={data} layout={layout} height={240} ariaLabel="Policy gap" />;
}

function InputsChart({ d }: { d: TaylorOut }) {
  const s = d.series;
  const data = useMemo(
    () => (t: Tokens): Data[] => [
      { type: "scatter", mode: "lines", name: "Core PCE inflation (y/y)", x: s.index, y: s.data.inflation, line: { color: t.categorical[2], width: 1.6 }, hovertemplate: "<b>Inflation</b> %{y:.2f}%<extra></extra>" } as Data,
      { type: "scatter", mode: "lines", name: "Output gap", x: s.index, y: s.data.output_gap, line: { color: t.categorical[4], width: 1.6 }, hovertemplate: "<b>Output gap</b> %{y:+.2f}%<extra></extra>" } as Data,
      { type: "scatter", mode: "lines", name: `π* = ${fmtNum(d.params.pi_star, 2)}%`, x: [s.index[0], s.index[s.index.length - 1]], y: [d.params.pi_star, d.params.pi_star], line: { color: t.categorical[2], width: 1, dash: "dash" }, hoverinfo: "skip" } as Data,
    ],
    [s, d.params.pi_star],
  );
  const layout = useMemo(() => (t: Tokens) => ({ hovermode: "x unified", xaxis: { type: "date", hoverformat: "%b %Y" }, yaxis: { ticksuffix: "%", side: "right", zeroline: true, zerolinecolor: t.ruleStrong }, margin: { l: 16, r: 8, t: 36, b: 28 } }) as any, []);
  return <Chart data={data} layout={layout} height={240} ariaLabel="Inflation and output gap" />;
}
