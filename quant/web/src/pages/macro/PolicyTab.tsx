/**
 * POLICY — GET /api/macro/taylor?r_star=&pi_star=&start=: Taylor (1993) and balanced-approach
 * prescriptions against the effective fed funds rate, the policy gap, and the rule's inputs
 * (core PCE y/y, CBO output gap). r* and π* are RULE PARAMETERS set here (defaults = Taylor's
 * 1993 calibration), labelled as assumptions, never presented as data.
 */
import { useState } from "react";
import { NumberField, Panel, SegmentedControl, StatGrid, StatTile } from "../../components";
import { XYChart } from "../../charts/XYChart";
import { Note } from "../../design";
import { fmtDate, fmtNum, fmtPctPoints } from "../../lib/format";
import { useDebounced } from "../../lib/hooks";
import { useApiQuery } from "../../lib/query";
import { INFO } from "./info";
import { Controls, Ctl, LiveOnly, Readline } from "./shared";
import type { TaylorOut } from "./types";

const STARTS = [
  { value: "1990", label: "1990–" },
  { value: "2000", label: "2000–" },
  { value: "2010", label: "2010–" },
  { value: "all", label: "ALL" },
] as const;
const LIVE_ITEMS = [
  { k: "TAYLOR 1993 · BALANCED APPROACH · VS EFFR", note: "taylor" },
  { k: "POLICY GAP · RULE − EFFR · PP", note: "taylor" },
  { k: "INPUTS · CORE PCE Y/Y · CBO OUTPUT GAP", note: "taylor" },
];

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
        isDefault ? (
          "TAYLOR 1993 · 2 / 2"
        ) : (
          <button type="button" className="btn btn-sm" onClick={() => (setRStar(2), setPiStar(2))}>
            RESET · 2 / 2
          </button>
        )
      }
    >
      <div className="mc-assume">
        <span className="mc-ctl-k">ASSUMED</span>
        <NumberField label="r*" value={rStar} onChange={setRStar} unit="%" min={-5} max={10} step={0.25} width={96} info={INFO.rstar} />
        <NumberField label="π*" value={piStar} onChange={setPiStar} unit="%" min={0} max={10} step={0.25} width={96} info={INFO.pistar} />
      </div>
      <Ctl label="FROM">
        <SegmentedControl size="sm" options={STARTS.map((s) => ({ value: s.value, label: s.label }))} value={start} onChange={setStart} ariaLabel="Start year" />
      </Ctl>
    </Controls>
  );

  if (q.isError && !q.data)
    return (
      <div className="stack">
        {controls}
        <LiveOnly title="POLICY RULES · FED FUNDS" error={q.error} source="/api/macro/taylor · FRED DFF PCEPILFE GDPC1 GDPPOT" items={LIVE_ITEMS} />
      </div>
    );

  const asOf = q.data?.latest.date;
  return (
    <div className="stack">
      {controls}
      <Panel<TaylorOut> query={q} skeletonHeight={96} notes={[]} provenance={[]} asOf={asOf}>
        {(d) => {
          const l = d.latest;
          const vs = (v: number | null | undefined) => (v != null ? `${fmtNum(v, 2, { signed: true })} PP VS EFFR` : undefined);
          return (
            <StatGrid min={150}>
              <StatTile size="sm" label="EFFR" value={fmtPctPoints(l.fed_funds)} caption={`${fmtDate(l.date, "month").toUpperCase()} AVG`} />
              <StatTile size="sm" label="TAYLOR 1993" info={INFO.taylor} value={fmtPctPoints(l.taylor_1993)} caption={vs(l.gap_taylor_minus_ff)} />
              <StatTile size="sm" label="BALANCED" info={INFO.balanced} value={fmtPctPoints(l.balanced_approach)} caption={vs(l.gap_balanced_minus_ff)} />
              <StatTile size="sm" label="CORE PCE Y/Y" info={INFO.yoy} value={fmtPctPoints(l.inflation)} caption={`π* ${fmtNum(d.params.pi_star, 2)}% ASSUMED`} />
              <StatTile size="sm" label="OUTPUT GAP" info={INFO.gap} value={`${fmtNum(l.output_gap, 2, { signed: true })}%`} caption="OF POTENTIAL · CBO" />
            </StatGrid>
          );
        }}
      </Panel>
      <Panel<TaylorOut>
        title={
          <>
            RULES VS EFFR · %
            <Note n={1} to="taylor" />
          </>
        }
        query={q}
        skeletonHeight={320}
        notes={[]}
        asOf={asOf}
      >
        {(d) => (
          <>
            <Readline items={[{ k: "r*", v: `${fmtNum(d.params.r_star, 2)}% ASSUMED` }, { k: "π*", v: `${fmtNum(d.params.pi_star, 2)}% ASSUMED` }, { k: "GAP COEF", v: `${fmtNum(d.params.gap_coef_taylor, 1)} · ${fmtNum(d.params.gap_coef_balanced, 1)}` }]} />
            <XYChart
              x={d.series.index}
              time
              series={[
                ...(d.series.data.fed_funds ? [{ name: "EFFR", y: d.series.data.fed_funds as (number | null)[], tone: "ink" as const, width: 1.75 }] : []),
                { name: "TAYLOR", y: d.series.data.taylor_1993 as (number | null)[], tone: "ink2" },
                { name: "BALANCED", y: d.series.data.balanced_approach as (number | null)[], tone: "ink3", dash: "dash" },
              ]}
              hlines={[{ at: 0, label: "0", tone: "ink3", dash: "dot" }]}
              yFormat="num"
              digits={2}
              height={300}
              ariaLabel="Taylor rule and balanced-approach prescriptions against the effective fed funds rate"
            />
          </>
        )}
      </Panel>
      <div className="grid-2">
        <Panel<TaylorOut> title="GAP · RULE − EFFR · PP" query={q} skeletonHeight={260} notes={[]} provenance={[]} asOf={asOf}>
          {(d) =>
            d.series.data.gap_taylor_minus_ff ? (
              <XYChart
                x={d.series.index}
                time
                series={[
                  { name: "TAYLOR", y: d.series.data.gap_taylor_minus_ff as (number | null)[], tone: "ink" },
                  ...(d.series.data.gap_balanced_minus_ff ? [{ name: "BALANCED", y: d.series.data.gap_balanced_minus_ff as (number | null)[], tone: "ink3" as const, dash: "dash" as const }] : []),
                ]}
                hlines={[{ at: 0, label: "0", tone: "ink", dash: "solid" }]}
                yFormat="num"
                digits={2}
                height={240}
                ariaLabel="Rule minus effective fed funds rate"
              />
            ) : (
              <div className="mc-none num">INSUFFICIENT DATA · EFFR UNAVAILABLE</div>
            )
          }
        </Panel>
        <Panel<TaylorOut> title="INPUTS · %" query={q} skeletonHeight={260} asOf={asOf}>
          {(d) => (
            <XYChart
              x={d.series.index}
              time
              series={[
                { name: "CORE PCE", y: d.series.data.inflation as (number | null)[], tone: "ink" },
                { name: "GAP", y: d.series.data.output_gap as (number | null)[], tone: "ink2", dash: "dash" },
              ]}
              hlines={[{ at: d.params.pi_star, label: "π*", tone: "ink3", dash: "dot" }]}
              yFormat="num"
              digits={2}
              height={240}
              ariaLabel="Core PCE inflation and the output gap"
            />
          )}
        </Panel>
      </div>
    </div>
  );
}
