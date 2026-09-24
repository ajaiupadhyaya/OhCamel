/**
 * Implied volatility (live Cboe chain; 503 offline):
 *  - the smile of one expiry — our IVs from bid/ask/mid against the SVI fit, vendor IV faint
 *    (GET /api/options/chain/{t}?expiry=);
 *  - across expiries (GET /api/options/surface/{t}): ATM and model-free term structure,
 *    25Δ/10Δ risk reversals & butterflies, the 3-D surface, static-arbitrage diagnostics and
 *    the parity-implied forward / rate / dividend yield of every expiry.
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, Formula, Panel, Section, SegmentedControl, StatGrid, StatTile, SurfaceChart, Toggle, withAlpha, type Column } from "../../components";
import { fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import type { Tokens } from "../../lib/theme";
import { INFO } from "./info";
import { Check, KV, expiryLabel, fmtDays, type useChain, type useSurface } from "./shared";
import type { CalendarCheck, Chain, SliceSummary, Surface, TermRow } from "./types";

const DAY_TICKS = [3, 7, 14, 30, 60, 90, 180, 365, 730];
const dayAxis = (days: number[]) => {
  const lo = Math.min(...days);
  const hi = Math.max(...days);
  const ticks = DAY_TICKS.filter((d) => d >= lo * 0.8 && d <= hi * 1.25);
  return { type: "log", tickvals: ticks, ticktext: ticks.map((d) => (d >= 365 ? `${d / 365}y` : `${d}d`)), title: { text: "Days to expiry" }, showspikes: false };
};

export function ImpliedTab({ surface, chain, expiry }: { surface: ReturnType<typeof useSurface>; chain: ReturnType<typeof useChain>; expiry: string | undefined }) {
  const s = surface.data;
  const row = s?.term_structure.find((r) => r.slice === expiry);
  return (
    <>
      <Panel<Surface>
        title="Implied volatility at a glance"
        subtitle={s ? `${s.underlying} ${s.exercise_style.toLowerCase()} options · spot ${fmtNum(s.spot, 2)} · ${s.term_structure.length} expiries fitted` : undefined}
        info={{ text: "Headline numbers from the fitted surface. The 30-day figures are constant-maturity: interpolated between the listed expiries either side of 30 days." }}
        query={surface}
        notes={[]}
        skeletonHeight={100}
      >
        {(d) => (
          <StatGrid min={150}>
            <StatTile label="30d ATM vol" value={d.atm_30d?.iv} format={(v) => fmtPct(v, 1)} info={INFO.atm_30d} caption={d.atm_30d?.extrapolated ? "extrapolated" : "SVI, interpolated"} />
            <StatTile label="30d model-free" value={d.vix_style_30d ? d.vix_style_30d.index / 100 : null} format={(v) => fmtPct(v, 1)} info={INFO.model_free} caption="VIX methodology" />
            <StatTile label={`ATM · ${row ? Math.round(row.dte) + "d" : "expiry"}`} value={row?.atm_iv} format={(v) => fmtPct(v, 1)} info={INFO.atm_iv} />
            <StatTile label="25Δ risk reversal" value={row?.rr_25d} format={(v) => `${fmtNum(v * 100, 2, { signed: true })} pts`} tone="auto" info={INFO.rr} />
            <StatTile label="25Δ butterfly" value={row?.bf_25d} format={(v) => `${fmtNum(v * 100, 2, { signed: true })} pts`} info={INFO.bf} />
            <StatTile label="Implied move" value={row?.implied_move} format={(v) => `±${fmtPct(v, 1)}`} info={INFO.implied_move} caption={row?.straddle != null ? `straddle ${fmtNum(row.straddle, 2)}` : undefined} />
          </StatGrid>
        )}
      </Panel>

      <Section title="Smile" description="One expiry at a time: every out-of-the-money quote's implied volatility (the whisker spans bid to ask), the SVI curve fitted through them, and the vendor's own IV in faint grey. Equity smiles slope down to the left because crash protection is in constant demand.">
        <Panel<Chain>
          title={chain.data ? `Smile · ${expiryLabel({ expiry: chain.data.slice.expiry, dte: chain.data.slice.dte, settlement: chain.data.slice.settlement })}` : "Smile"}
          subtitle="Out-of-the-money puts below the forward, calls above it (the liquid side of each strike)."
          info={INFO.svi}
          query={chain}
          skeletonHeight={420}
        >
          {(c) => <SmilePanel c={c} />}
        </Panel>
      </Section>

      <Section title="Across expiries" description="How the price of volatility changes with the horizon. An upward-sloping term structure (contango) is the calm-market norm; inversion — short-dated vol above long-dated — signals stress happening now.">
        <div className="grid-2">
          <Panel<Surface> title="Term structure" subtitle="ATM-forward vol from each SVI fit, and model-free vol from the whole strip of OTM options." info={INFO.atm_iv} query={surface} notes={[]} skeletonHeight={320}>
            {(d) => <TermChart d={d} expiry={expiry} />}
          </Panel>
          <Panel<Surface> title="Skew & convexity" subtitle="Risk reversals (skew: puts over calls) and butterflies (smile curvature) at 25Δ and 10Δ, in vol points." info={INFO.rr} query={surface} notes={[]} skeletonHeight={320}>
            {(d) => <SkewChart rows={d.term_structure} />}
          </Panel>
        </div>
        <Panel<Surface> title="Volatility surface" subtitle="Implied vol over log-moneyness k = ln(K/F) and time to expiry. Between listed expiries, total variance is interpolated linearly in T at fixed k; nothing is extrapolated. Drag to rotate." info={{ ...INFO.svi, title: "SVI surface" }} query={surface} notes={[]} skeletonHeight={520}>
          {(d) => (
            <SurfaceChart
              x={d.grid.k.map((k) => +k.toFixed(4))}
              y={d.grid.days.map((x) => +x.toFixed(1))}
              z={d.grid.iv}
              zFormat="pct"
              titles={{ x: "k = ln(K/F)", y: "Days", z: "IV" }}
              height={520}
              layout={{ scene: { camera: { eye: { x: 1.35, y: -1.45, z: 0.7 } }, aspectratio: { x: 1.3, y: 1.3, z: 0.75 } } } as never}
            />
          )}
        </Panel>
      </Section>

      <Section title="Is the surface arbitrage-free?" description="A fitted surface is only usable for pricing if no combination of options would be a free lunch. Two static checks cover it: across strikes (butterflies must cost something) and across maturities (longer options must hold more total variance).">
        <Panel<Surface> title="Static-arbitrage diagnostics" info={INFO.butterfly_g} query={surface} flush skeletonHeight={300}>
          {(d) => <ArbPanel d={d} />}
        </Panel>
      </Section>

      <Section title="What the options imply about carry" description="Before any volatility can be computed, each expiry's forward price and discount factor are read off the options themselves through put-call parity. That gives the market's own funding rate and dividend (or borrow) yield per expiry — no outside yield curve required.">
        <Panel<Surface>
          title="Implied forward, rate & dividend yield"
          subtitle={<span className="vx-inline-formula">Regress call − put on strike at near-the-money strikes: <Formula inline tex="C(K) - P(K) = D\,(F - K)" />. Slope → discount factor D, intercept → forward F.</span>}
          info={INFO.parity}
          query={surface}
          flush
          notes={[]}
          skeletonHeight={300}
        >
          {(d) => <ParityTable rows={d.expiries} spot={d.spot} european={d.exercise_style === "European"} />}
        </Panel>
      </Section>
    </>
  );
}

// ------------------------------------------------------------------ smile
function SmilePanel({ c }: { c: Chain }) {
  const [axis, setAxis] = useState<"strike" | "k">("strike");
  const [showAll, setShowAll] = useState(false);
  const F = c.slice.forward;
  const fit = c.smile?.fit;
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const X = (q: { strike: number; k: number | null }) => (axis === "strike" ? q.strike : q.k);
      const traces: unknown[] = [];
      const used = c.quotes.filter((q) => q.use_smile && q.iv != null);
      const other = c.quotes.filter((q) => !q.use_smile && q.valid && q.iv != null);
      const vendor = c.quotes.filter((q) => q.use_smile && q.vendor_iv != null);
      if (vendor.length)
        traces.push({ type: "scatter", mode: "markers", name: "Vendor IV", x: vendor.map(X), y: vendor.map((q) => q.vendor_iv), marker: { size: 5, color: withAlpha(t.text3, 0.45), symbol: "circle-open" }, hovertemplate: "vendor %{y:.2%}<extra></extra>" });
      if (showAll && other.length)
        traces.push({ type: "scatter", mode: "markers", name: "In-the-money / filtered", x: other.map(X), y: other.map((q) => q.iv), marker: { size: 5, color: withAlpha(t.text3, 0.35), symbol: "x-thin", line: { width: 1, color: withAlpha(t.text3, 0.6) } }, customdata: other.map((q) => [q.type === "C" ? "call" : "put", q.strike]), hovertemplate: "%{customdata[0]} %{customdata[1]} · %{y:.2%}<extra>not in fit</extra>" });
      for (const [typ, name, color] of [["P", "OTM puts", t.categorical[1]], ["C", "OTM calls", t.categorical[2]]] as const) {
        const qs = used.filter((q) => q.type === typ);
        if (!qs.length) continue;
        traces.push({
          type: "scatter",
          mode: "markers",
          name,
          x: qs.map(X),
          y: qs.map((q) => q.iv),
          marker: { size: 6, color, line: { color: t.surface, width: 1 } },
          error_y: { type: "data", symmetric: false, array: qs.map((q) => (q.iv_ask != null ? q.iv_ask - (q.iv as number) : 0)), arrayminus: qs.map((q) => (q.iv_bid != null ? (q.iv as number) - q.iv_bid : 0)), color: withAlpha(color, 0.55), thickness: 1, width: 0 },
          customdata: qs.map((q) => [q.strike, q.k, q.iv_bid, q.iv_ask, q.vendor_iv]),
          hovertemplate: "<b>%{y:.2%}</b> mid<br>K %{customdata[0]:,.2f} · k %{customdata[1]:+.3f}<br>bid %{customdata[2]:.2%} · ask %{customdata[3]:.2%}<extra>" + name + "</extra>",
        });
      }
      if (c.smile)
        traces.push({ type: "scatter", mode: "lines", name: "SVI fit", x: axis === "strike" ? c.smile.strike : c.smile.k, y: c.smile.iv, line: { color: t.categorical[0], width: 2.2, shape: "spline" }, hovertemplate: "SVI %{y:.2%}<extra></extra>" });
      return traces as Data[];
    },
    [c, axis, showAll],
  );
  const layout = useMemo(
    () => (t: Tokens) => {
      const x0 = axis === "strike" ? F : 0;
      const xs = axis === "strike" ? c.spot : Math.log(c.spot / F);
      return {
        hovermode: "closest",
        xaxis: { title: { text: axis === "strike" ? "Strike" : "Log-moneyness k = ln(K/F)" }, tickformat: axis === "strike" ? ",.0f" : "+.2f", zeroline: false },
        yaxis: { tickformat: ".0%", side: "right" },
        shapes: [
          { type: "line", yref: "paper", y0: 0, y1: 1, x0, x1: x0, line: { color: t.ruleStrong, width: 1, dash: "dot" } },
          ...(Math.abs(xs - x0) > 1e-9 ? [{ type: "line", yref: "paper", y0: 0, y1: 1, x0: xs, x1: xs, line: { color: t.text3, width: 1, dash: "dash" } }] : []),
        ],
        annotations: [
          { x: x0, yref: "paper", y: 1, text: "forward", showarrow: false, xanchor: "left", yanchor: "top", xshift: 4, font: { size: 10, color: t.text3 } },
          ...(Math.abs(xs - x0) > 1e-9 ? [{ x: xs, yref: "paper", y: 0.92, text: "spot", showarrow: false, xanchor: xs < x0 ? "right" : "left", yanchor: "top", xshift: xs < x0 ? -4 : 4, font: { size: 10, color: t.text3 } }] : []),
        ],
        margin: { l: 16, r: 8, t: 36, b: 44 },
      };
    },
    [axis, F, c.spot],
  );
  const bfly = fit?.butterfly;
  return (
    <div className="vx-smile">
      <div className="vx-smile-main">
        <div className="vx-toolbar">
          <SegmentedControl size="sm" ariaLabel="Smile x-axis" options={[{ value: "strike", label: "Strike" }, { value: "k", label: "ln(K/F)" }]} value={axis} onChange={setAxis} />
          <Toggle label="Show in-the-money & filtered quotes" checked={showAll} onChange={setShowAll} />
        </div>
        <Chart data={data} layout={layout as never} height={400} />
      </div>
      <aside className="vx-smile-side">
        <div className="vx-side-title">Fit quality</div>
        {fit ? (
          <>
            <div className="row-wrap">
              <Check ok={bfly?.arbitrage_free} label="Butterfly-free" title={`min g(k) = ${fmtNum(bfly?.g_min, 4)}`} />
              <Check ok={fit.rmse_vol_pts < 1} label={`RMSE ${fmtNum(fit.rmse_vol_pts, 3)} pts`} title="Root-mean-square IV error of the fit, in vol points; under 1 point is a tight fit" />
            </div>
            <KV
              rows={[
                { label: "Quotes in fit", value: fmtNum(fit.n, 0), info: { text: "Out-of-the-money quotes that passed the filters (two-sided, not too wide, within no-arbitrage bounds)." } },
                { label: "Max |error|", value: `${fmtNum(fit.max_abs_err_vol_pts, 3)} pts` },
                { label: "min g(k)", value: fmtNum(bfly?.g_min, 4), info: INFO.butterfly_g, tone: bfly?.arbitrage_free ? "" : "loss" },
                { label: "Vendor IV gap (MAD)", value: c.slice.vendor_iv_mad != null ? `${fmtNum(c.slice.vendor_iv_mad * 100, 2)} pts` : "—", info: INFO.vendor_iv },
              ]}
            />
            <div className="vx-side-title">SVI parameters</div>
            <KV
              cols={2}
              rows={[
                { label: "a", value: fmtNum(fit.params.a, 5), info: { text: "Overall level of total variance." } },
                { label: "b", value: fmtNum(fit.params.b, 4), info: { text: "Slope of the wings (how fast variance grows away from the money)." } },
                { label: "ρ", value: fmtNum(fit.params.rho, 3), info: { text: "Rotation / skew, between −1 and 1. Negative: the left (put) wing is steeper." } },
                { label: "m", value: fmtNum(fit.params.m, 4), info: { text: "Horizontal shift of the smile's vertex in log-moneyness." } },
                { label: "σ", value: fmtNum(fit.params.sigma, 4), info: { text: "Curvature (ATM smoothness): small σ gives a sharp V, large σ a rounded smile." } },
              ]}
            />
            <KV rows={[{ label: "Wing slopes (left / right)", value: `${fmtNum(fit.wing_slopes[0], 3)} / ${fmtNum(fit.wing_slopes[1], 3)}`, info: { text: "b(1 − ρ) and b(1 + ρ): Lee's moment formula requires both ≤ 2 for finite moments.", reference: "Lee (2004), “The Moment Formula for Implied Volatility at Extreme Strikes”, Math. Finance 14(3)" } }]} />
          </>
        ) : (
          <p className="subtle small">No SVI fit for this expiry — see the notes below.</p>
        )}
      </aside>
    </div>
  );
}

// ------------------------------------------------------------------ term structure & skew
function TermChart({ d, expiry }: { d: Surface; expiry: string | undefined }) {
  const rows = d.term_structure;
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const x = rows.map((r) => r.dte);
      const tr: unknown[] = [
        { type: "scatter", mode: "lines+markers", name: "ATM (SVI)", x, y: rows.map((r) => r.atm_iv ?? null), line: { color: t.categorical[0], width: 2 }, marker: { size: rows.map((r) => (r.slice === expiry ? 11 : 6)), color: t.categorical[0], line: { color: t.surface, width: 1.5 } }, customdata: rows.map((r) => r.expiry), hovertemplate: "ATM <b>%{y:.2%}</b><extra>%{customdata} · %{x:.0f}d</extra>", connectgaps: true },
        { type: "scatter", mode: "lines+markers", name: "Model-free", x, y: rows.map((r) => r.mf_vol ?? null), line: { color: t.categorical[3], width: 1.6, dash: "dash" }, marker: { size: 5, color: t.categorical[3] }, customdata: rows.map((r) => r.expiry), hovertemplate: "model-free <b>%{y:.2%}</b><extra>%{customdata}</extra>", connectgaps: true },
        { type: "scatter", mode: "markers", name: "ATM (raw quotes)", x, y: rows.map((r) => r.atm_iv_market ?? null), marker: { size: 5, color: withAlpha(t.text3, 0.6), symbol: "circle-open" }, hovertemplate: "market ATM %{y:.2%}<extra></extra>" },
      ];
      const pts30 = [
        d.atm_30d && { y: d.atm_30d.iv, name: "30d ATM", color: t.categorical[0] },
        d.vix_style_30d && { y: d.vix_style_30d.index / 100, name: "30d model-free", color: t.categorical[3] },
      ].filter(Boolean) as { y: number; name: string; color: string }[];
      for (const p of pts30) tr.push({ type: "scatter", mode: "markers", name: p.name, x: [30], y: [p.y], marker: { symbol: "star", size: 12, color: p.color, line: { color: t.surface, width: 1 } }, hovertemplate: `${p.name} <b>%{y:.2%}</b><extra></extra>`, showlegend: false });
      return tr as Data[];
    },
    [rows, d.atm_30d, d.vix_style_30d, expiry],
  );
  const layout = useMemo(() => ({ hovermode: "closest", xaxis: dayAxis(rows.map((r) => r.dte)), yaxis: { tickformat: ".0%", side: "right" }, margin: { l: 16, r: 8, t: 36, b: 44 } }), [rows]);
  return (
    <>
      <Chart data={data} layout={layout as never} height={300} />
      <p className="vx-foot-note subtle small">Stars mark the 30-day constant-maturity values. Model-free vol includes the tails, so it sits above ATM when puts are bid.</p>
    </>
  );
}

function SkewChart({ rows }: { rows: TermRow[] }) {
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const x = rows.map((r) => r.dte);
      const s = (key: keyof TermRow, name: string, color: string, dash?: string) => ({ type: "scatter", mode: "lines+markers", name, x, y: rows.map((r) => (r[key] as number | null | undefined) ?? null), line: { color, width: dash ? 1.4 : 2, dash }, marker: { size: dash ? 4 : 6, color }, customdata: rows.map((r) => r.expiry), hovertemplate: `${name} <b>%{y:+.2%}</b><extra>%{customdata}</extra>`, connectgaps: true });
      return [s("rr_25d", "RR 25Δ", t.categorical[1]), s("rr_10d", "RR 10Δ", t.categorical[1], "dot"), s("bf_25d", "BF 25Δ", t.categorical[4]), s("bf_10d", "BF 10Δ", t.categorical[4], "dot")] as Data[];
    },
    [rows],
  );
  const layout = useMemo(() => (t: Tokens) => ({ hovermode: "closest", xaxis: dayAxis(rows.map((r) => r.dte)), yaxis: { tickformat: "+.1%", side: "right", zeroline: true, zerolinecolor: t.ruleStrong }, margin: { l: 16, r: 8, t: 36, b: 44 } }), [rows]);
  return (
    <>
      <Chart data={data} layout={layout as never} height={300} />
      <p className="vx-foot-note subtle small">Risk reversal below zero: puts are dearer than calls. The 10Δ lines look further into the tails.</p>
    </>
  );
}

// ------------------------------------------------------------------ arbitrage
function ArbPanel({ d }: { d: Surface }) {
  const smiles = d.smiles;
  const nbOk = smiles.filter((s) => s.fit.butterfly.arbitrage_free).length;
  const ncOk = d.calendar.filter((c) => !c.violation).length;
  type BRow = { slice: string; dte: number; n: number; rmse: number; g: number | null; kg: number | null; ok: boolean };
  const brows: BRow[] = smiles.map((s) => ({ slice: s.slice, dte: s.dte, n: s.fit.n, rmse: s.fit.rmse_vol_pts, g: s.fit.butterfly.g_min, kg: s.fit.butterfly.k_at_g_min, ok: s.fit.butterfly.arbitrage_free }));
  const bcols: Column<BRow>[] = [
    { key: "slice", label: "Expiry", render: (r) => <span className="num">{r.slice}</span> },
    { key: "dte", label: "Days", numeric: true, format: fmtDays },
    { key: "n", label: "Quotes", numeric: true, format: (v) => fmtNum(v, 0), hideBelow: 600 },
    { key: "rmse", label: "RMSE (pts)", numeric: true, format: (v) => fmtNum(v, 3), info: { text: "Root-mean-square gap between the fitted SVI vol and the market mid IVs, in vol points." } },
    { key: "g", label: "min g(k)", numeric: true, format: (v) => fmtNum(v, 4), info: INFO.butterfly_g, color: (v) => (v != null && v < 0 ? "loss" : undefined) },
    { key: "kg", label: "at k", numeric: true, format: (v) => fmtNum(v, 3), hideBelow: 900 },
    { key: "ok", label: "Butterfly", sortable: false, render: (r) => <Check ok={r.ok} label={r.ok ? "pass" : "fail"} /> },
  ];
  const Tlabel = (T: number) => {
    const s = smiles.find((x) => Math.abs(x.T - T) < 1e-9);
    return s ? s.slice : `${fmtNum(T * 365, 0)}d`;
  };
  const ccols: Column<CalendarCheck>[] = [
    { key: "T1", label: "Pair", render: (c) => <span className="num">{Tlabel(c.T1)} → {Tlabel(c.T2)}</span> },
    { key: "min_dw", label: "min Δw", numeric: true, format: (v) => (v == null ? "—" : v.toExponential(2)), info: { text: "Smallest increase in total variance from the nearer to the farther expiry across the strike grid. Must be ≥ 0.", formula: "\\min_k\\,[w(k, T_2) - w(k, T_1)]" }, color: (v) => (v != null && v < 0 ? "loss" : undefined) },
    { key: "k_at_min", label: "at k", numeric: true, format: (v) => fmtNum(v, 3), hideBelow: 900 },
    { key: "violation", label: "Calendar", sortable: false, render: (c) => <Check ok={!c.violation} label={c.violation ? "fail" : "pass"} /> },
  ];
  return (
    <div className="vx-arb">
      <div className="vx-arb-summary">
        <Check ok={nbOk === smiles.length} label={`Butterfly · ${nbOk}/${smiles.length} expiries`} />
        <Check ok={d.calendar.length ? ncOk === d.calendar.length : null} label={`Calendar · ${ncOk}/${d.calendar.length} pairs`} />
        <span className="subtle small">{d.method.arbitrage}</span>
      </div>
      <div className="vx-arb-grid">
        <div>
          <div className="vx-table-title">Across strikes (per expiry)</div>
          <DataTable columns={bcols} rows={brows} rowKey={(r) => r.slice} />
        </div>
        <div>
          <div className="vx-table-title">Across maturities (adjacent pairs)</div>
          <DataTable columns={ccols} rows={d.calendar} rowKey={(c) => `${c.T1}-${c.T2}`} />
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ parity
function ParityTable({ rows, spot, european }: { rows: SliceSummary[]; spot: number; european: boolean }) {
  const cols: Column<SliceSummary>[] = [
    { key: "slice", label: "Expiry", render: (r) => <span className="num">{r.slice}{r.settlement === "AM" && <span className="badge" style={{ marginLeft: 6 }}>AM</span>}</span> },
    { key: "dte", label: "Days", numeric: true, format: fmtDays },
    { key: "forward", label: "Forward F", numeric: true, format: (v) => fmtNum(v, 2) },
    { key: "carry", label: "F / S − 1", numeric: true, value: (r) => r.forward / spot - 1, format: (v) => fmtSignedPct(v, 2), info: { text: "Carry to expiry: the forward's premium over spot, set by interest minus dividends.", formula: "F/S - 1 = e^{(r-q)T} - 1" }, hideBelow: 900 },
    { key: "discount", label: "Discount D", numeric: true, format: (v) => fmtNum(v, 5), hideBelow: 1200 },
    { key: "rate", label: "Rate r", numeric: true, format: (v) => fmtPct(v, 2), info: { text: "Continuously compounded rate implied by the discount factor.", formula: "r = -\\ln D / T" } },
    { key: "div_yield", label: "Div. yield q", numeric: true, format: (v) => fmtPct(v, 2), info: { text: "Continuous dividend (or stock-borrow) yield implied by the forward.", formula: "q = r - \\ln(F/S)/T" } },
    {
      key: "rate_source",
      label: "r from",
      render: (r) => <span className={`badge ${r.rate_source === "regression" ? "accent" : ""}`} title={r.rate_source === "regression" ? "Slope of this expiry's own parity regression" : "Too few strikes pin the slope down here; the rate is borrowed from neighbouring expiries"}>{r.rate_source}</span>,
    },
    { key: "rate_se", label: "SE(r)", numeric: true, format: (v) => (v == null ? "—" : fmtPct(v, 2)), hideBelow: 1200, info: { text: "Standard error of the regression rate; large values mean the slope is poorly identified." } },
    { key: "parity_rmse", label: "Fit RMSE", numeric: true, format: (v) => fmtNum(v, 3), hideBelow: 900, info: { text: "Root-mean-square residual of the parity regression, in price units." } },
    { key: "n_valid", label: "Valid / listed", numeric: true, render: (r) => <span className="num">{r.n_valid} <span className="subtle">/ {r.n_contracts}</span></span>, hideBelow: 600 },
  ];
  return (
    <>
      <DataTable columns={cols} rows={rows} rowKey={(r) => r.slice} maxHeight={440} />
      <p className="vx-foot-note subtle small">
        {european
          ? "European exercise: parity holds exactly, so these are clean market-implied carry parameters."
          : "American exercise: early-exercise value makes parity an approximation; it is fitted on near-the-money strikes where that value is smallest."}{" "}
        Every implied vol on this page uses its own expiry's F and D.
      </p>
    </>
  );
}
