/**
 * CURVE — GET /api/macro/curve?date=&compare=: the Treasury par (CMT) curve against comparison
 * dates, the bootstrapped zero curve with instantaneous and 1-year forwards, and the
 * Nelson–Siegel / Svensson fits (parameters, RMSE, residuals). Without the full FRED curve the
 * tab reads INSUFFICIENT DATA and lists what it computes.
 */
import { useMemo, useState } from "react";
import { BarChart, DataTable, Panel, StatGrid, StatTile, type Column } from "../../components";
import { CurveChart, type CurveLine } from "../../charts/CurveChart";
import { XYChart } from "../../charts/XYChart";
import { Note } from "../../design";
import { fmtDate, fmtNum, fmtPctPoints, toIsoDate } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import { alignNumeric, tenorLabel, tenorTicks } from "./derive";
import { INFO } from "./info";
import { Controls, Ctl, KV, LiveOnly, Readline, Sub, fmtBpFromPp } from "./shared";
import type { CompareCurve, CurveFit, CurveOut } from "./types";

const OFFSETS = ["1W", "1M", "3M", "6M", "1Y", "2Y", "5Y"] as const;
const DEFAULT_COMPARE = ["1M", "1Y"];

export function useCurve(date: string | undefined, compare: string[]) {
  return useApiQuery<CurveOut>("/macro/curve", { date, compare: compare.join(",") });
}

/** Par yield at a tenor (exact match). */
const parAt = (c: { par?: { tenors: number[]; yields: number[] } } | undefined, t: number) => {
  const i = c?.par?.tenors.findIndex((x) => Math.abs(x - t) < 1e-6) ?? -1;
  return i >= 0 ? c!.par!.yields[i] : null;
};

const LIVE_ITEMS = [
  { k: "PAR · CMT · NOW VS COMPARISON DATES", note: "curve" },
  { k: "Δ BY TENOR · BP", note: "curve" },
  { k: "ZERO · BOOTSTRAP · INST + 1Y FORWARD", note: "curve" },
  { k: "NELSON–SIEGEL · SVENSSON · RMSE · RESIDUALS", note: "curve" },
];

export function CurveTab() {
  const [date, setDate] = useState<string>("");
  const [compare, setCompare] = useState<string[]>(DEFAULT_COMPARE);
  const [custom, setCustom] = useState("");
  const q = useCurve(date || undefined, compare);
  const toggle = (o: string) => setCompare((c) => (c.includes(o) ? c.filter((x) => x !== o) : [...c, o].slice(-5)));

  const controls = (
    <Controls right={q.data ? `CURVE ${fmtDate(q.data.date).toUpperCase()}` : undefined}>
      <Ctl label="DATE">
        <input type="date" className="input num" value={date} max={toIsoDate(new Date())} min="1990-01-02" onChange={(e) => setDate(e.target.value)} aria-label="Curve date (blank = latest)" />
        {date && (
          <button type="button" className="btn btn-sm" onClick={() => setDate("")}>
            LATEST
          </button>
        )}
      </Ctl>
      <Ctl label="VS">
        <div className="mc-chips" role="group" aria-label="Comparison curves">
          {OFFSETS.map((o) => (
            <button key={o} type="button" className={`mc-chip num ${compare.includes(o) ? "on" : ""}`} aria-pressed={compare.includes(o)} onClick={() => toggle(o)}>
              {o}
            </button>
          ))}
          {compare
            .filter((c) => !(OFFSETS as readonly string[]).includes(c))
            .map((c) => (
              <button key={c} type="button" className="mc-chip num on" onClick={() => toggle(c)} aria-label={`Remove ${c}`}>
                {c} ×
              </button>
            ))}
          <input
            type="date"
            className="input num mc-chip-date"
            value={custom}
            max={toIsoDate(new Date())}
            onChange={(e) => {
              const v = e.target.value;
              setCustom("");
              if (v && !compare.includes(v)) setCompare((c) => [...c, v].slice(-5));
            }}
            aria-label="Add a comparison date"
          />
        </div>
      </Ctl>
    </Controls>
  );

  if (q.isError && !q.data)
    return (
      <div className="stack">
        {controls}
        <LiveOnly title="CURVE · UST" error={q.error} source="/api/macro/curve · FRED DGS1MO…DGS30" items={LIVE_ITEMS} />
      </div>
    );

  const asOf = q.data?.date;
  return (
    <div className="stack">
      {controls}
      <Panel<CurveOut> query={q} skeletonHeight={96} notes={[]} provenance={[]} asOf={asOf}>
        {(d) => <Headline d={d} />}
      </Panel>
      <div className="grid-3">
        <Panel<CurveOut>
          title={
            <>
              PAR · CMT
              <Note n={1} to="curve" />
            </>
          }
          query={q}
          span={2}
          skeletonHeight={300}
          notes={[]}
          asOf={asOf}
        >
          {(d) => <CurveChart lines={parLines(d)} height={300} ariaLabel="Treasury par curve against the comparison dates" />}
        </Panel>
        <Panel<CurveOut> title="Δ BY TENOR · BP" query={q} skeletonHeight={300} notes={[]} provenance={[]} flush asOf={asOf}>
          {(d) => <ChangeTable d={d} />}
        </Panel>
      </div>
      <Panel<CurveOut>
        title={
          <>
            PAR · ZERO · FORWARD
            <Note n={2} to="curve" />
          </>
        }
        query={q}
        skeletonHeight={320}
        notes={[]}
        provenance={[]}
        asOf={asOf}
      >
        {(d) => <ThreeCurves d={d} />}
      </Panel>
      <Panel<CurveOut>
        title={
          <>
            NELSON–SIEGEL · SVENSSON
            <Note n={3} to="curve" />
          </>
        }
        query={q}
        skeletonHeight={320}
        asOf={asOf}
      >
        {(d) => <Fits d={d} />}
      </Panel>
    </div>
  );
}

export function parLines(d: CurveOut): CurveLine[] {
  const out: CurveLine[] = [{ key: "now", label: "NOW", tenors: d.par.tenors, yields: d.par.yields }];
  for (const c of d.compare) if (!c.error && c.par) out.push({ key: c.label, label: c.label, tenors: c.par.tenors, yields: c.par.yields });
  return out;
}

function Headline({ d }: { d: CurveOut }) {
  const ref = d.compare.find((c) => !c.error);
  const y = (t: number) => parAt(d, t);
  const r = (t: number) => (ref ? parAt(ref, t) : null);
  const delta = (t: number) => {
    const a = y(t);
    const b = r(t);
    return a != null && b != null ? a - b : null;
  };
  const sp = (a: number, b: number) => {
    const A = y(a);
    const B = y(b);
    return A != null && B != null ? A - B : null;
  };
  const lbl = ref ? `VS ${ref.label}` : undefined;
  const s2s10 = sp(10, 2);
  const s3m10 = sp(10, 0.25);
  const tile = (t: number, label: string) => <StatTile size="sm" label={label} value={fmtPctPoints(y(t))} caption={delta(t) != null ? `${fmtBpFromPp(delta(t))} ${lbl}` : undefined} />;
  const slope = (v: number | null, label: string, info: typeof INFO.s2s10) => (
    <StatTile size="sm" label={label} info={info} value={fmtBpFromPp(v)} tone={v != null && v < 0 ? "loss" : "neutral"} caption={v != null ? <span className={v < 0 ? "loss" : ""}>{v < 0 ? "INVERTED" : "POSITIVE"}</span> : undefined} />
  );
  return (
    <StatGrid min={130}>
      {tile(0.25, "3M")}
      {tile(2, "2Y")}
      {tile(10, "10Y")}
      {tile(30, "30Y")}
      {slope(s2s10, "2S10S", INFO.s2s10)}
      {slope(s3m10, "3M10Y", INFO.s3m10y)}
    </StatGrid>
  );
}

type ChangeRow = { t: number; now: number | null; [k: string]: number | null };

function ChangeTable({ d }: { d: CurveOut }) {
  const ok = d.compare.filter((c): c is CompareCurve & { change_bp: Record<string, number> } => !c.error && !!c.change_bp);
  const failed = d.compare.filter((c) => c.error);
  const rows: ChangeRow[] = d.par.tenors.map((t, i) => {
    const row: ChangeRow = { t, now: d.par.yields[i] ?? null };
    ok.forEach((c, j) => {
      const hit = Object.entries(c.change_bp).find(([k]) => Math.abs(+k - t) < 1e-6);
      row[`c${j}`] = hit ? hit[1] : null;
    });
    return row;
  });
  const cols: Column<ChangeRow>[] = [
    { key: "t", label: "Tenor", render: (r) => <span className="num">{tenorLabel(r.t)}</span> },
    { key: "now", label: "Par", numeric: true, format: (v) => fmtPctPoints(v) },
    ...ok.map((c, j) => ({ key: `c${j}`, label: `Δ ${c.label}`, numeric: true, format: (v: number | null) => (v == null ? "—" : fmtNum(v, 0, { signed: true })) })),
  ];
  return (
    <>
      <DataTable<ChangeRow> columns={cols} rows={rows} rowKey={(r) => r.t} compact />
      {failed.length > 0 && <div className="mc-none num mc-pad">NO CURVE · {failed.map((f) => f.label).join(" · ")}</div>}
    </>
  );
}

function ThreeCurves({ d }: { d: CurveOut }) {
  const c = d.curve;
  const a = useMemo(
    () =>
      alignNumeric([
        { x: d.par.tenors, y: d.par.yields },
        { x: c.t, y: c.zero },
        { x: c.t, y: c.forward_1y },
        { x: c.t, y: c.inst_forward },
      ]),
    [d, c],
  );
  const lo = Math.min(...a.x.filter((v) => v > 0));
  const df10 = c.discount[c.t.findIndex((x) => Math.abs(x - 10) < 1e-6)];
  return (
    <>
      <Readline items={[{ k: "ZERO · FWD", v: "CONT. COMP." }, { k: "PAR", v: "SEMIANNUAL BEY" }, { k: "DF 10Y", v: fmtNum(df10, 4) }]} />
      <XYChart
        x={a.x}
        logX
        xTicks={tenorTicks(lo, 30)}
        series={[
          { name: "PAR", y: a.ys[0], mode: "points", tone: "ink", size: 5 },
          { name: "ZERO", y: a.ys[1], tone: "ink", span: true },
          { name: "FWD 1Y", y: a.ys[2], tone: "ink2", dash: "dash", span: true },
          { name: "FWD INST", y: a.ys[3], tone: "ink3", dash: "dot", span: true },
        ]}
        yFormat="pctPoints"
        digits={2}
        height={300}
        ariaLabel="Par yields, zero curve and forward curves by maturity"
      />
    </>
  );
}

const PARAMS: { key: string; label: string }[] = [
  { key: "b0", label: "β₀ LEVEL" },
  { key: "b1", label: "β₁ SLOPE" },
  { key: "b2", label: "β₂ HUMP" },
  { key: "b3", label: "β₃ HUMP 2" },
  { key: "tau", label: "τ" },
  { key: "tau1", label: "τ₁" },
  { key: "tau2", label: "τ₂" },
];

function Fits({ d }: { d: CurveOut }) {
  const ns = d.fits.nelson_siegel;
  const nss = d.fits.svensson;
  const good = (f?: CurveFit): f is CurveFit => !!f && !f.error;
  const a = useMemo(
    () =>
      alignNumeric([
        { x: d.nodes.t, y: d.nodes.zero },
        { x: good(ns) ? ns.fitted.t : [], y: good(ns) ? ns.fitted.zero : [] },
        { x: good(nss) ? nss.fitted.t : [], y: good(nss) ? nss.fitted.zero : [] },
      ]),
    [d, ns, nss],
  );
  type Row = { key: string; label: string; ns: number | null; nss: number | null };
  const rows: Row[] = PARAMS.map((p) => ({ ...p, ns: ns?.params?.[p.key] ?? null, nss: nss?.params?.[p.key] ?? null })).filter((r) => r.ns != null || r.nss != null);
  const cols: Column<Row>[] = [
    { key: "label", label: "Param", render: (r) => <span className="num mc-sym">{r.label}</span> },
    { key: "ns", label: "NS", numeric: true, format: (v) => fmtNum(v, 3), info: INFO.ns },
    { key: "nss", label: "NSS", numeric: true, format: (v) => fmtNum(v, 3), info: INFO.nss },
  ];
  const lo = Math.min(...a.x.filter((v) => v > 0));
  const resid = [good(ns) && { name: "NS", x: ns.residuals_bp.t.map(tenorLabel), y: ns.residuals_bp.bp }, good(nss) && { name: "NSS", x: nss.residuals_bp.t.map(tenorLabel), y: nss.residuals_bp.bp }].filter(Boolean) as { name: string; x: string[]; y: number[] }[];
  const binding = [ns, nss].some((f) => f?.binding_constraints?.length);
  return (
    <div className="mc-split">
      <div className="mc-split-main">
        <XYChart
          x={a.x}
          logX
          xTicks={tenorTicks(lo, 30)}
          series={[
            { name: "ZERO NODES", y: a.ys[0], mode: "points", tone: "ink2", size: 5 },
            ...(good(ns) ? [{ name: "NS", y: a.ys[1], tone: "ink" as const, span: true }] : []),
            ...(good(nss) ? [{ name: "NSS", y: a.ys[2], tone: "ink" as const, dash: "dash" as const, span: true }] : []),
          ]}
          yFormat="pctPoints"
          digits={2}
          height={300}
          ariaLabel="Bootstrapped zero nodes with Nelson–Siegel and Svensson fits"
        />
        {resid.length > 0 && (
          <>
            <Sub>RESIDUAL · ZERO − FIT · BP</Sub>
            <BarChart series={resid} yFormat="num" digits={1} height={180} />
          </>
        )}
      </div>
      <div className="mc-split-side">
        <DataTable<Row> columns={cols} rows={rows} rowKey={(r) => r.key} compact />
        <KV
          rows={[
            { k: "RMSE · NS", v: good(ns) ? `${fmtNum(ns.rmse_bp, 2)} BP` : (d.fits.nelson_siegel?.error ?? "—"), info: INFO.rmse },
            { k: "RMSE · NSS", v: good(nss) ? `${fmtNum(nss.rmse_bp, 2)} BP` : (d.fits.svensson?.error ?? "—"), info: INFO.rmse },
            { k: "STARTS", v: `${ns?.starts ?? "—"} / ${nss?.starts ?? "—"}` },
            ...(binding ? [{ k: "SIGN RESTRICTION", v: "BINDING" }] : []),
          ]}
        />
      </div>
    </div>
  );
}
