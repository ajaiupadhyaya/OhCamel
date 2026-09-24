/**
 * Yield curve — GET /api/macro/curve?date=&compare=: Treasury par (CMT) curve, the
 * bootstrapped zero curve, instantaneous and 1-year forwards, Nelson–Siegel and Svensson
 * fits (params, RMSE, residuals) and comparison curves (offsets like 1M/1Y or ISO dates).
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, Panel, StatGrid, StatTile, type Column } from "../../components";
import { fmtBps, fmtDate, fmtNum, fmtPctPoints, toIsoDate } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import { INFO } from "./info";
import { Bars, Controls, KV, MethodCard, tenorAxis, tenorLabel } from "./shared";
import type { CompareCurve, CurveFit, CurveOut } from "./types";

const STD_LABELS = new Set(["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]);
const OFFSETS = ["1W", "1M", "3M", "6M", "1Y", "2Y", "5Y"] as const;
const DEFAULT_COMPARE = ["1M", "1Y"];

export function useCurve(date: string | undefined, compare: string[]) {
  return useApiQuery<CurveOut>("/macro/curve", { date, compare: compare.join(",") });
}

/** par yield at a tenor (exact match) */
const parAt = (c: { par?: { tenors: number[]; yields: number[] } } | undefined, t: number) => {
  const i = c?.par?.tenors.findIndex((x) => Math.abs(x - t) < 1e-6) ?? -1;
  return i >= 0 ? c!.par!.yields[i] : null;
};

export function CurveTab() {
  const [date, setDate] = useState<string>("");
  const [compare, setCompare] = useState<string[]>(DEFAULT_COMPARE);
  const [custom, setCustom] = useState("");
  const q = useCurve(date || undefined, compare);
  const toggle = (o: string) => setCompare((c) => (c.includes(o) ? c.filter((x) => x !== o) : [...c, o].slice(-5)));

  const controls = (
    <Controls
      right={
        <span className="subtle small">
          {q.data ? (
            <>
              Curve of <span className="num">{fmtDate(q.data.date)}</span>
            </>
          ) : null}
        </span>
      }
    >
      <label className="mc-inline-field">
        <span className="oc-field-label">Curve date</span>
        <input type="date" className="input num" value={date} max={toIsoDate(new Date())} min="1990-01-02" onChange={(e) => setDate(e.target.value)} aria-label="Curve date (blank = latest)" />
        {date && (
          <button type="button" className="btn btn-sm btn-ghost" onClick={() => setDate("")}>
            Latest
          </button>
        )}
      </label>
      <div className="mc-inline-field">
        <span className="oc-field-label">Compare with</span>
        <div className="mc-chips" role="group" aria-label="Comparison curves">
          {OFFSETS.map((o) => (
            <button key={o} type="button" className={`mc-chip num ${compare.includes(o) ? "on" : ""}`} aria-pressed={compare.includes(o)} onClick={() => toggle(o)}>
              {o} ago
            </button>
          ))}
          {compare
            .filter((c) => !(OFFSETS as readonly string[]).includes(c))
            .map((c) => (
              <button key={c} type="button" className="mc-chip num on" onClick={() => toggle(c)} title="Remove">
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
            title="Add a specific date"
          />
        </div>
      </div>
    </Controls>
  );

  const method = (
    <MethodCard
      title="From quoted yields to a full curve"
      formulas={["1 = \\tfrac{c_T}{2}\\sum_{k=1}^{2T} P(\\tfrac k2) + P(T),\\quad z(T) = -\\tfrac{\\ln P(T)}{T}", "f(T) = z(T) + T\\,z'(T)"]}
      refs={["Hull, Options, Futures & Other Derivatives, ch. 4", "Fritsch & Carlson (1980), SIAM J. Numer. Anal. — PCHIP", "Nelson & Siegel (1987); Svensson (1994)", "Gürkaynak, Sack & Wright (2007), JME 54(8)"]}
    >
      The Treasury publishes <em>par</em> yields at 11 maturities. Treating each as a bond priced at 100, we strip out the <em>zero</em> rate for every maturity (bootstrapping), then read off the <em>forward</em> rates the market is implicitly locking in. Nelson–Siegel and Svensson compress the whole curve into a level, a slope and one or two humps.
    </MethodCard>
  );

  if (q.isError && !q.data) {
    return (
      <div className="stack">
        {controls}
        <div className="grid-3">
          <Panel title="Treasury yield curve" subtitle="Par, zero and forward curves with Nelson–Siegel and Svensson fits." query={q} span={2} />
          {method}
        </div>
      </div>
    );
  }

  return (
    <div className="stack">
      {controls}
      <Panel<CurveOut> query={q} skeletonHeight={96} notes={[]} provenance={[]}>
        {(d) => <Headline d={d} />}
      </Panel>
      <div className="grid-3">
        <Panel<CurveOut>
          title="Par curve: now vs then"
          subtitle="Treasury yields by maturity. An upward slope is normal (lenders want more to lock money up longer); an inverted curve — short yields above long — says markets expect rates to fall."
          info={INFO.par}
          query={q}
          span={2}
          skeletonHeight={380}
          notes={[]}
        >
          {(d) => <ParCompare d={d} />}
        </Panel>
        <Panel<CurveOut> title="Change by maturity" subtitle="How far each yield has moved since each comparison date, in basis points (1 bp = 0.01%)." query={q} skeletonHeight={380} notes={[]} provenance={[]} info={{ text: "Today's par yield minus the par yield on the comparison date, per maturity. A bigger rise at the short end than the long end is a bear flattener; the opposite is a bear steepener." }}>
          {(d) => <ChangeBars compare={d.compare} />}
        </Panel>
      </div>
      <div className="grid-3">
        <Panel<CurveOut>
          title="Par, zero and forward curves"
          subtitle="The same curve three ways. Zero rates discount a single payment; forwards are the rates locked in today for future borrowing — when forwards sit below spot, the market is pricing cuts."
          info={INFO.forward}
          query={q}
          span={2}
          skeletonHeight={380}
        >
          {(d) => <ThreeCurves d={d} />}
        </Panel>
        {method}
      </div>
      <Panel<CurveOut>
        title="Nelson–Siegel & Svensson fits"
        subtitle="Smooth parametric curves fitted to the bootstrapped zero rates — how central banks publish yield curves. Residuals show where each quoted maturity trades rich (below the fit) or cheap (above)."
        info={INFO.nss}
        query={q}
        skeletonHeight={380}
        notes={[]}
        provenance={[]}
      >
        {(d) => <Fits d={d} />}
      </Panel>
    </div>
  );
}

function Headline({ d }: { d: CurveOut }) {
  const ref = d.compare.find((c) => !c.error);
  const y = (t: number) => parAt(d, t);
  const r = (t: number) => (ref ? parAt(ref, t) : null);
  const sp = (a: number, b: number, src: (t: number) => number | null) => {
    const A = src(a);
    const B = src(b);
    return A != null && B != null ? 100 * (A - B) : null;
  };
  const dl = (a: number | null, b: number | null) => (a != null && b != null ? a - b : null);
  const bp = (v: number) => fmtBps(v / 1e4, 0, { signed: true });
  const lbl = ref ? `vs ${ref.label}` : undefined;
  const s2s10 = sp(10, 2, y);
  const s3m10 = sp(10, 0.25, y);
  return (
    <StatGrid min={140}>
      <StatTile label="3-month" value={fmtPctPoints(y(0.25))} delta={dl(y(0.25), r(0.25)) != null ? dl(y(0.25), r(0.25))! * 100 : null} deltaFormat={bp} deltaLabel={lbl} info={INFO.par} />
      <StatTile label="2-year" value={fmtPctPoints(y(2))} delta={dl(y(2), r(2)) != null ? dl(y(2), r(2))! * 100 : null} deltaFormat={bp} deltaLabel={lbl} />
      <StatTile label="10-year" value={fmtPctPoints(y(10))} delta={dl(y(10), r(10)) != null ? dl(y(10), r(10))! * 100 : null} deltaFormat={bp} deltaLabel={lbl} />
      <StatTile label="30-year" value={fmtPctPoints(y(30))} delta={dl(y(30), r(30)) != null ? dl(y(30), r(30))! * 100 : null} deltaFormat={bp} deltaLabel={lbl} />
      <StatTile label="2s10s" value={s2s10 != null ? bp(s2s10) : null} tone={s2s10 != null && s2s10 < 0 ? "loss" : "neutral"} info={INFO.s2s10} caption={s2s10 != null ? (s2s10 < 0 ? "inverted" : "positive slope") : undefined} />
      <StatTile label="3m10y" value={s3m10 != null ? bp(s3m10) : null} tone={s3m10 != null && s3m10 < 0 ? "loss" : "neutral"} info={INFO.s3m10y} caption={s3m10 != null ? (s3m10 < 0 ? "inverted" : "positive slope") : undefined} />
    </StatGrid>
  );
}

const hoverTenor = (fmt = ".2f", unit = "%") => `%{customdata}  <b>%{y:${fmt}}${unit}</b><extra>%{fullData.name}</extra>`;

function ParCompare({ d }: { d: CurveOut }) {
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const ok = d.compare.filter((c) => !c.error && c.par);
      const tr = (c: { par?: { tenors: number[]; yields: number[] }; date?: string }, name: string, color: string, main: boolean) =>
        ({
          type: "scatter",
          mode: "lines+markers",
          name,
          x: c.par!.tenors,
          y: c.par!.yields,
          customdata: c.par!.tenors.map(tenorLabel),
          line: { color, width: main ? 2.4 : 1.6, dash: main ? "solid" : "dot", shape: "spline", smoothing: 0.6 },
          marker: { size: main ? 7 : 5, color, line: { width: main ? 1.5 : 0, color: t.surface } },
          hovertemplate: hoverTenor(),
        }) as Data;
      return [...ok.map((c, i) => tr(c, `${c.label} ago · ${fmtDate(c.date)}`, t.categorical[i + 1], false)), tr(d, `Now · ${fmtDate(d.date)}`, t.categorical[0], true)];
    },
    [d],
  );
  const layout = useMemo(() => ({ xaxis: tenorAxis(), yaxis: { ticksuffix: "%", tickformat: ".2f", side: "right" }, hovermode: "x unified", legend: { traceorder: "reversed" }, margin: { l: 16, r: 8, t: 36, b: 40 } }) as any, []);
  return <Chart data={data} layout={layout} height={360} ariaLabel="Par yield curve comparison" />;
}

function ChangeBars({ compare }: { compare: CompareCurve[] }) {
  const ok = compare.filter((c) => !c.error && c.change_bp);
  const failed = compare.filter((c) => c.error);
  if (!ok.length) return <div className="subtle small">{failed.length ? failed.map((f) => `${f.label}: ${f.error}`).join("; ") : "Pick a comparison date above."}</div>;
  const tenors = Object.keys(ok[0].change_bp!).map(Number).sort((a, b) => a - b);
  const series = ok.map((c) => {
    const m = new Map(Object.entries(c.change_bp!).map(([k, v]) => [+k, v]));
    return { name: `vs ${c.label}`, x: tenors.map(tenorLabel), y: tenors.map((t) => m.get(t) ?? null) };
  });
  return (
    <>
      <Bars series={series} unit=" bp" height={340} />
      {failed.length > 0 && <div className="subtle small">No curve for {failed.map((f) => f.label).join(", ")}.</div>}
    </>
  );
}

function ThreeCurves({ d }: { d: CurveOut }) {
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const c = d.curve;
      const lab = c.t.map(tenorLabel);
      return [
        { type: "scatter", mode: "markers", name: "Par (quoted)", x: d.par.tenors, y: d.par.yields, customdata: d.par.tenors.map(tenorLabel), marker: { size: 8, color: t.surface, line: { width: 2, color: t.categorical[0] } }, hovertemplate: hoverTenor() },
        { type: "scatter", mode: "lines", name: "Zero (spot)", x: c.t, y: c.zero, customdata: lab, line: { color: t.categorical[0], width: 2.2 }, hovertemplate: hoverTenor() },
        { type: "scatter", mode: "lines", name: "1-year forward", x: c.t, y: c.forward_1y, customdata: lab, line: { color: t.categorical[1], width: 1.8, dash: "dash" }, hovertemplate: hoverTenor() },
        { type: "scatter", mode: "lines", name: "Instantaneous forward", x: c.t, y: c.inst_forward, customdata: lab, line: { color: t.categorical[2], width: 1.4, dash: "dot" }, hovertemplate: hoverTenor() },
      ] as Data[];
    },
    [d],
  );
  const layout = useMemo(() => ({ xaxis: tenorAxis([0.25, 30]), yaxis: { ticksuffix: "%", tickformat: ".2f", side: "right" }, hovermode: "x unified", margin: { l: 16, r: 8, t: 36, b: 40 } }) as any, []);
  return (
    <>
      <Chart data={data} layout={layout} height={360} ariaLabel="Par, zero and forward curves" />
      <div className="subtle small">Zero and forward rates are continuously compounded; par yields are semiannual bond-equivalent. Discount factor at 10 years: <span className="num">{fmtNum(d.curve.discount[d.curve.t.findIndex((x) => Math.abs(x - 10) < 1e-6)], 4)}</span>.</div>
    </>
  );
}

const PARAM_ROWS: { key: string; label: string; text: string }[] = [
  { key: "b0", label: "β₀ level", text: "Long-run level the curve approaches at very long maturities (%)." },
  { key: "b1", label: "β₁ slope", text: "Short end minus long end: negative β₁ means an upward-sloping curve (%)." },
  { key: "b2", label: "β₂ hump", text: "Size of the medium-term hump (+) or trough (−) (%)." },
  { key: "b3", label: "β₃ 2nd hump", text: "Svensson's second hump, for a bend further out the curve (%)." },
  { key: "tau", label: "τ decay", text: "Where the hump peaks, in years." },
  { key: "tau1", label: "τ₁ decay", text: "Where the first hump peaks, in years." },
  { key: "tau2", label: "τ₂ decay", text: "Where the second hump peaks, in years." },
];

function Fits({ d }: { d: CurveOut }) {
  const ns = d.fits.nelson_siegel;
  const nss = d.fits.svensson;
  const good = (f?: CurveFit) => f && !f.error;
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const out: Data[] = [{ type: "scatter", mode: "markers", name: "Bootstrapped zero (nodes)", x: d.nodes.t, y: d.nodes.zero, customdata: d.nodes.t.map(tenorLabel), marker: { size: 7, color: t.text2 }, hovertemplate: hoverTenor() } as Data];
      if (good(ns)) out.push({ type: "scatter", mode: "lines", name: `Nelson–Siegel · ${fmtNum(ns!.rmse_bp, 1)} bp RMSE`, x: ns!.fitted.t, y: ns!.fitted.zero, customdata: ns!.fitted.t.map(tenorLabel), line: { color: t.categorical[1], width: 2 }, hovertemplate: hoverTenor() } as Data);
      if (good(nss)) out.push({ type: "scatter", mode: "lines", name: `Svensson · ${fmtNum(nss!.rmse_bp, 1)} bp RMSE`, x: nss!.fitted.t, y: nss!.fitted.zero, customdata: nss!.fitted.t.map(tenorLabel), line: { color: t.categorical[0], width: 2, dash: "dash" }, hovertemplate: hoverTenor() } as Data);
      return out;
    },
    [d, ns, nss],
  );
  const layout = useMemo(() => ({ xaxis: tenorAxis(), yaxis: { ticksuffix: "%", tickformat: ".2f", side: "right" }, hovermode: "x unified", margin: { l: 16, r: 8, t: 36, b: 40 } }) as any, []);
  const resid = [good(ns) && { name: "Nelson–Siegel", x: ns!.residuals_bp.t.map(tenorLabel), y: ns!.residuals_bp.bp }, good(nss) && { name: "Svensson", x: nss!.residuals_bp.t.map(tenorLabel), y: nss!.residuals_bp.bp }].filter(Boolean) as { name: string; x: string[]; y: number[] }[];
  type Row = { key: string; label: string; text: string; ns: number | null; nss: number | null };
  const rows: Row[] = PARAM_ROWS.map((p) => ({ ...p, ns: ns?.params?.[p.key] ?? null, nss: nss?.params?.[p.key] ?? null })).filter((r) => r.ns != null || r.nss != null);
  const cols: Column<Row>[] = [
    { key: "label", label: "Parameter", render: (r) => <span title={r.text}>{r.label}</span> },
    { key: "ns", label: "Nelson–Siegel", numeric: true, format: (v) => fmtNum(v, 3), info: INFO.ns },
    { key: "nss", label: "Svensson", numeric: true, format: (v) => fmtNum(v, 3), info: INFO.nss },
  ];
  return (
    <div className="stack">
      <div className="mc-fit-grid">
        <div>
          <Chart data={data} layout={layout} height={340} ariaLabel="Parametric curve fits" />
        </div>
        <div className="stack">
          <DataTable columns={cols} rows={rows} rowKey={(r) => r.key} />
          <KV
            rows={[
              { k: "RMSE · Nelson–Siegel", v: good(ns) ? `${fmtNum(ns!.rmse_bp, 2)} bp` : ns?.error ?? "—", info: INFO.rmse },
              { k: "RMSE · Svensson", v: good(nss) ? `${fmtNum(nss!.rmse_bp, 2)} bp` : nss?.error ?? "—", info: INFO.rmse },
              { k: "Optimizer starts", v: `${ns?.starts ?? "—"} / ${nss?.starts ?? "—"}`, muted: true },
            ]}
          />
          {[ns, nss].some((f) => f?.binding_constraints?.length) && <div className="subtle small">A sign restriction is binding (see notes): read the fitted curve, not β₀, as the long end.</div>}
        </div>
      </div>
      {resid.length > 0 && (
        <div>
          <div className="mc-subhead">Residuals at each quoted maturity (zero minus fit, bp)</div>
          <Bars series={resid} unit=" bp" tick=",.1f" hover=",.2f" height={200} layout={{ xaxis: { tickvals: resid[0]?.x.filter((l) => STD_LABELS.has(l)), tickangle: 0 } }} />
        </div>
      )}
    </div>
  );
}
