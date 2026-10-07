/**
 * RECESSION — GET /api/macro/recession?h=&extra=: the Estrella–Mishkin term-spread probit
 * (optionally + NFCI), fitted probability indexed by the TARGET month with NBER recessions
 * shaded, the current h-month-ahead probability, HAC coefficients, the spread itself and the
 * Sahm rule.
 */
import { useState } from "react";
import { DataTable, Panel, SegmentedControl, StatGrid, StatTile, Toggle, type Column } from "../../components";
import { XYChart } from "../../charts/XYChart";
import { Note } from "../../design";
import { fmtDate, fmtNum, fmtPct } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import { nberBands } from "./derive";
import { INFO } from "./info";
import { Controls, Ctl, KV, LiveOnly, Readline } from "./shared";
import type { ProbitModel, RecessionOut } from "./types";

const HORIZONS = ["6", "12", "18", "24"] as const;
const RANGES = [
  { value: "all", label: "ALL" },
  { value: "1990", label: "1990–" },
  { value: "2005", label: "2005–" },
] as const;
const LIVE_ITEMS = [
  { k: "P(NBER RECESSION · t+h) · PROBIT · 10Y−3M", note: "recession" },
  { k: "+ NFCI · HAC STANDARD ERRORS · PSEUDO-R²", note: "recession" },
  { k: "SAHM RULE · 3M AVG UNRATE − 12M LOW", note: "recession" },
];
const HAC_INFO = { title: "Newey–West s.e.", text: "Heteroskedasticity- and autocorrelation-consistent standard error; overlapping horizons make plain errors too small.", reference: "Newey & West (1987), Econometrica 55(3)" };

/** Clip a date-indexed series to start at `from`. */
function clip<T>(index: (string | number)[], cols: T[][], from?: string): { x: (string | number)[]; ys: T[][] } {
  if (!from) return { x: index, ys: cols };
  const i0 = index.findIndex((d) => String(d) >= from);
  const k = i0 < 0 ? index.length : i0;
  return { x: index.slice(k), ys: cols.map((c) => c.slice(k)) };
}

export function RecessionTab() {
  const [h, setH] = useState<string>("12");
  const [nfci, setNfci] = useState(true);
  const [range, setRange] = useState<string>("all");
  const q = useApiQuery<RecessionOut>("/macro/recession", { h: +h, extra: nfci ? "nfci" : "none" });
  const from = range === "all" ? undefined : `${range}-01-01`;

  const controls = (
    <Controls>
      <Ctl label="HORIZON">
        <SegmentedControl size="sm" options={HORIZONS.map((x) => ({ value: x, label: `${x}M` }))} value={h} onChange={setH} ariaLabel="Forecast horizon (months)" />
      </Ctl>
      <Toggle label="+ NFCI" checked={nfci} onChange={setNfci} />
      <Ctl label="RANGE">
        <SegmentedControl size="sm" options={RANGES.map((r) => ({ value: r.value, label: r.label }))} value={range} onChange={setRange} ariaLabel="Chart range" />
      </Ctl>
    </Controls>
  );

  if (q.isError && !q.data)
    return (
      <div className="stack">
        {controls}
        <LiveOnly title={`RECESSION · ${h}M AHEAD`} error={q.error} source="/api/macro/recession · FRED USREC T10Y3M NFCI UNRATE" items={LIVE_ITEMS} />
      </div>
    );

  const asOf = q.data?.current.origin;
  return (
    <div className="stack">
      {controls}
      <Panel<RecessionOut> query={q} skeletonHeight={96} notes={[]} provenance={[]} asOf={asOf}>
        {(d) => <Current d={d} h={h} />}
      </Panel>
      <Panel<RecessionOut>
        title={
          <>
            P(RECESSION) · {h}M · AT TARGET MONTH
            <Note n={1} to="recession" />
          </>
        }
        query={q}
        skeletonHeight={300}
        notes={[]}
        asOf={asOf}
      >
        {(d) => <ProbHistory d={d} from={from} />}
      </Panel>
      <div className="grid-3">
        <Panel<RecessionOut> title="PROBIT · HAC" query={q} skeletonHeight={260} notes={[]} provenance={[]} flush>
          {(d) => <Coefs d={d} />}
        </Panel>
        <Panel<RecessionOut> title="10Y − 3M · MONTHLY · PP" query={q} span={2} skeletonHeight={260} notes={[]} provenance={[]} asOf={asOf}>
          {(d) => {
            const c = clip(d.spread.index, [d.spread.values as (number | null)[]], from);
            return <XYChart x={c.x} time series={[{ name: "10Y−3M", y: c.ys[0], tone: "ink" }]} hlines={[{ at: 0, label: "0", tone: "ink", dash: "solid" }]} bands={nberBands(d.recessions, from)} yFormat="num" digits={2} height={240} ariaLabel="10-year minus 3-month Treasury spread, NBER recessions shaded" />;
          }}
        </Panel>
      </div>
      <Panel<RecessionOut>
        title={
          <>
            SAHM RULE · PP
            <Note n={2} to="recession" />
          </>
        }
        query={q}
        skeletonHeight={280}
      >
        {(d) => (d.sahm ? <Sahm d={d} from={from} /> : <div className="mc-none num">INSUFFICIENT DATA · UNRATE UNAVAILABLE</div>)}
      </Panel>
    </div>
  );
}

function Current({ d, h }: { d: RecessionOut; h: string }) {
  const p = d.current.probability;
  const p2 = d.current.probability_with_nfci;
  const m = d.models.spread;
  return (
    <StatGrid min={150}>
      <StatTile size="lg" label={`P(REC) · ${h}M`} info={INFO.probit} value={fmtPct(p, 0)} tone={p >= 0.5 ? "loss" : "neutral"} caption={`TARGET ${fmtDate(d.current.target, "month").toUpperCase()}`} />
      {p2 != null && <StatTile size="lg" label="+ NFCI" info={INFO.nfci} value={fmtPct(p2, 0)} tone={p2 >= 0.5 ? "loss" : "neutral"} caption={d.current.nfci_origin ? `NFCI ${fmtDate(d.current.nfci_origin, "month").toUpperCase()}` : undefined} />}
      <StatTile size="lg" label="10Y − 3M" info={INFO.s3m10y} value={`${fmtNum(d.current.spread, 2, { signed: true })} PP`} tone={d.current.spread < 0 ? "loss" : "neutral"} caption={`ORIGIN ${fmtDate(d.current.origin, "month").toUpperCase()}`} />
      <StatTile size="lg" label="R² · ESTRELLA" info={INFO.pseudoR2} value={fmtNum(m.pseudo_r2_estrella, 3)} caption={`${fmtDate(d.sample.start, "year")}–${fmtDate(d.sample.end, "year")} · N ${fmtNum(d.sample.nobs, 0)}`} />
    </StatGrid>
  );
}

function ProbHistory({ d, from }: { d: RecessionOut; from?: string }) {
  const P = d.probability;
  const cols = [P.data.spread_model as (number | null)[], ...(P.data.spread_nfci_model ? [P.data.spread_nfci_model as (number | null)[]] : [])];
  const c = clip(P.index, cols, from);
  return (
    <>
      <Readline items={[{ k: "SHADED", v: "NBER RECESSION" }, { k: "EPISODES", v: fmtNum(nberBands(d.recessions, from).length, 0) }]} />
      <XYChart
        x={c.x}
        time
        series={[{ name: "SPREAD", y: c.ys[0], tone: "ink" }, ...(c.ys[1] ? [{ name: "+NFCI", y: c.ys[1], tone: "ink2" as const, dash: "dot" as const }] : [])]}
        hlines={[{ at: 0.5, label: "50%", tone: "ink3", dash: "dot" }]}
        bands={nberBands(d.recessions, from)}
        zero
        yFormat="pct"
        digits={0}
        height={280}
        ariaLabel="Fitted recession probability at the target month, NBER recessions shaded"
      />
    </>
  );
}

const REG_LABEL: Record<string, string> = { const: "α", spread: "β SPREAD", nfci: "β NFCI" };

function Coefs({ d }: { d: RecessionOut }) {
  type Row = { id: string; model: string; param: string; coef: number; se: number; p: number };
  const rows: Row[] = [];
  const add = (key: string, name: string, m?: ProbitModel) => {
    if (!m) return;
    for (const k of Object.keys(m.params)) rows.push({ id: `${key}-${k}`, model: name, param: REG_LABEL[k] ?? k.toUpperCase(), coef: m.params[k], se: m.std_errors[k], p: m.pvalues[k] });
  };
  add("s", "SPREAD", d.models.spread);
  add("n", "+NFCI", d.models.spread_nfci);
  const cols: Column<Row>[] = [
    { key: "model", label: "Model", render: (r) => <span className="num">{r.model}</span> },
    { key: "param", label: "Term", render: (r) => <span className="num mc-sym">{r.param}</span> },
    { key: "coef", label: "Coef", numeric: true, format: (v) => fmtNum(v, 2, { signed: true }) },
    { key: "se", label: "s.e.", numeric: true, format: (v) => fmtNum(v, 2), hideBelow: 600, info: HAC_INFO },
    { key: "p", label: "p", numeric: true, render: (r) => <span className={r.p < 0.05 ? "mc-strong" : ""}>{r.p < 0.001 ? "<0.001" : fmtNum(r.p, 3)}</span> },
  ];
  const m = d.models.spread;
  return (
    <>
      <DataTable<Row> columns={cols} rows={rows} rowKey={(r) => r.id} compact />
      <div className="mc-pad">
        <KV
          rows={[
            { k: "R² · MCFADDEN", v: fmtNum(m.pseudo_r2_mcfadden, 3), info: INFO.pseudoR2 },
            { k: "LOGLIK · NULL", v: `${fmtNum(m.loglik, 1)} · ${fmtNum(m.loglik_null, 1)}` },
            { k: "N", v: fmtNum(m.nobs, 0) },
          ]}
        />
      </div>
    </>
  );
}

function Sahm({ d, from }: { d: RecessionOut; from?: string }) {
  const s = d.sahm!;
  const c = clip(s.series.index, [s.series.data.sahm as (number | null)[]], from);
  return (
    <>
      <Readline
        items={[
          { k: "LAST", v: `${fmtNum(s.latest.value, 2)} PP · ${fmtDate(s.latest.date, "month").toUpperCase()}`, tone: s.latest.triggered ? "loss" : "" },
          { k: "STATE", v: s.latest.triggered ? "TRIGGERED" : "BELOW", tone: s.latest.triggered ? "loss" : "" },
          s.trigger_dates.length > 0 && { k: "TRIGGERS", v: s.trigger_dates.map((x) => fmtDate(x, "year")).join(" · ") },
        ]}
      />
      <XYChart x={c.x} time series={[{ name: "SAHM", y: c.ys[0], tone: "ink" }]} hlines={[{ at: s.threshold, label: `${fmtNum(s.threshold, 2)}`, tone: "signal", dash: "dash" }]} bands={nberBands(d.recessions, from)} yFormat="num" digits={2} height={260} ariaLabel="Sahm rule indicator against its trigger, NBER recessions shaded" />
    </>
  );
}
