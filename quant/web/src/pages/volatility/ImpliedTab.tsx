/**
 * SURFACE (live Cboe chain; 503 offline):
 *  - one expiry's smile: our mid IVs with bid–ask whiskers, the SVI fit, vendor IV in ink-3
 *    (GET /api/options/chain/{t}?expiry=);
 *  - across expiries (GET /api/options/surface/{t}): ATM and model-free term structure,
 *    25Δ / 10Δ risk reversals and butterflies, the 3-D surface (the app's one Plotly view,
 *    lazy), static-arbitrage checks, and the parity-implied forward / rate / yield per expiry.
 */
import { lazy, Suspense, useMemo, useState } from "react";
import { ChartSkeleton, DataTable, Panel, Section, SegmentedControl, StatGrid, StatTile, Toggle, type Column } from "../../components";
import { XYChart, type XYSeries } from "../../charts/XYChart";
import { Note } from "../../design";
import { fmtNum, fmtPct, fmtSci, fmtSignedPct } from "../../lib/format";
import { dayTicks } from "./derive";
import { INFO } from "./info";
import { Check, KV, Readline, expiryLabel, fmtDays, fmtVolPts, type useChain, type useSurface } from "./shared";
import type { CalendarCheck, Chain, SliceSummary, Surface, TermRow } from "./types";

const SurfaceView = lazy(() => import("./SurfaceView"));

export function ImpliedTab({ surface, chain, expiry }: { surface: ReturnType<typeof useSurface>; chain: ReturnType<typeof useChain>; expiry: string | undefined }) {
  const s = surface.data;
  const row = s?.term_structure.find((r) => r.slice === expiry);
  const asOf = s?.as_of;
  return (
    <>
      <Panel<Surface> title={`Surface · ${row ? fmtDays(row.dte) : "30D"}`} query={surface} notes={[]} skeletonHeight={100} asOf={asOf}>
        {(d) => (
          <>
            <Readline
              items={[
                { k: "SPOT", v: fmtNum(d.spot, 2) },
                { k: "EXPIRIES", v: fmtNum(d.term_structure.length, 0) },
                { k: "STYLE", v: d.exercise_style.toUpperCase() },
              ]}
            />
            <StatGrid min={150}>
              <StatTile label="ATM · 30D" value={d.atm_30d?.iv} format={(v) => fmtPct(v, 1)} info={INFO.atm_30d} caption={d.atm_30d?.extrapolated ? "EXTRAPOLATED" : "SVI · INTERPOLATED"} />
              <StatTile label="Model-free · 30D" value={d.vix_style_30d ? d.vix_style_30d.index / 100 : null} format={(v) => fmtPct(v, 1)} info={INFO.model_free} caption="CBOE METHOD" />
              <StatTile label={`ATM · ${row ? fmtDays(row.dte) : "—"}`} value={row?.atm_iv} format={(v) => fmtPct(v, 1)} info={INFO.atm_iv} />
              <StatTile label="RR 25Δ · pts" value={row?.rr_25d} format={(v) => fmtVolPts(v, 2)} info={INFO.rr} />
              <StatTile label="BF 25Δ · pts" value={row?.bf_25d} format={(v) => fmtVolPts(v, 2)} info={INFO.bf} />
              <StatTile label="Implied move" value={row?.implied_move} format={(v) => `±${fmtPct(v, 1)}`} info={INFO.implied_move} caption={row?.straddle != null ? `STRADDLE ${fmtNum(row.straddle, 2)}` : undefined} />
            </StatGrid>
          </>
        )}
      </Panel>

      <Section
        title={
          <>
            Smile · SVI
            <Note n={1} to="svi" />
          </>
        }
      >
        <Panel<Chain> title={chain.data ? `Smile · ${expiryLabel({ expiry: chain.data.slice.expiry, dte: chain.data.slice.dte, settlement: chain.data.slice.settlement })}` : "Smile"} info={INFO.svi} query={chain} skeletonHeight={420} asOf={chain.data?.as_of}>
          {(c) => <SmilePanel c={c} />}
        </Panel>
      </Section>

      <Section
        title={
          <>
            Term · skew
            <Note n={2} to="smile-metrics" />
          </>
        }
      >
        <div className="grid-2">
          <Panel<Surface> title="ATM · model-free · by expiry" info={INFO.atm_iv} query={surface} notes={[]} skeletonHeight={320} asOf={asOf}>
            {(d) => <TermChart d={d} />}
          </Panel>
          <Panel<Surface> title="RR · BF · 25Δ · 10Δ" info={INFO.rr} query={surface} notes={[]} skeletonHeight={320} asOf={asOf}>
            {(d) => <SkewChart rows={d.term_structure} />}
          </Panel>
        </div>
        <Panel<Surface> title="Surface · IV over k × days" info={INFO.svi} query={surface} notes={[]} skeletonHeight={520} asOf={asOf}>
          {(d) => (
            <Suspense fallback={<ChartSkeleton height={520} />}>
              <SurfaceView k={d.grid.k} days={d.grid.days} iv={d.grid.iv} height={520} />
            </Suspense>
          )}
        </Panel>
      </Section>

      <Section
        title={
          <>
            Static arbitrage
            <Note n={3} to="svi" />
          </>
        }
      >
        <Panel<Surface> title="Butterfly · calendar" info={INFO.butterfly_g} query={surface} flush skeletonHeight={300} asOf={asOf}>
          {(d) => <ArbPanel d={d} />}
        </Panel>
      </Section>

      <Section
        title={
          <>
            Carry · put-call parity
            <Note n={4} to="implied-vol" />
          </>
        }
      >
        <Panel<Surface> title="Forward · rate · yield" info={INFO.parity} query={surface} flush notes={[d_note(s)]} skeletonHeight={300} asOf={asOf}>
          {(d) => <ParityTable rows={d.expiries} spot={d.spot} />}
        </Panel>
      </Section>
    </>
  );
}

const d_note = (s: Surface | undefined) =>
  s?.exercise_style === "American" ? "American exercise: parity is approximate; fitted on near-the-money strikes where early-exercise value is smallest." : "European exercise: parity holds exactly.";

// ------------------------------------------------------------------ smile
function SmilePanel({ c }: { c: Chain }) {
  const [axis, setAxis] = useState<"strike" | "k">("strike");
  const [showAll, setShowAll] = useState(false);
  const F = c.slice.forward;
  const fit = c.smile?.fit;
  const { x, series } = useMemo(() => {
    const X = (q: { strike: number; k: number | null }) => (axis === "strike" ? q.strike : q.k);
    const used = c.quotes.filter((q) => q.use_smile && q.iv != null && X(q) != null);
    const other = showAll ? c.quotes.filter((q) => !q.use_smile && q.valid && q.iv != null && X(q) != null) : [];
    const fitX = c.smile ? (axis === "strike" ? c.smile.strike : c.smile.k) : [];
    const xs = [...new Set([...used.map(X), ...other.map(X), ...fitX].filter((v): v is number => v != null && Number.isFinite(v)))].sort((a, b) => a - b);
    const idx = new Map(xs.map((v, i) => [v, i]));
    const col = () => xs.map((): number | null => null);
    const put = col();
    const putLo = col();
    const putHi = col();
    const call = col();
    const callLo = col();
    const callHi = col();
    const vendor = col();
    const itm = col();
    const svi = col();
    for (const q of used) {
      const i = idx.get(X(q) as number)!;
      if (q.type === "P") [put[i], putLo[i], putHi[i]] = [q.iv, q.iv_bid, q.iv_ask];
      else [call[i], callLo[i], callHi[i]] = [q.iv, q.iv_bid, q.iv_ask];
      if (q.vendor_iv != null) vendor[i] = q.vendor_iv;
    }
    for (const q of other) itm[idx.get(X(q) as number)!] = q.iv;
    fitX.forEach((v, j) => (svi[idx.get(v)!] = c.smile!.iv[j]));
    const out: XYSeries[] = [
      { name: "VENDOR", y: vendor, mode: "points", tone: "ink3", size: 3 },
      ...(showAll ? [{ name: "ITM · FILTERED", y: itm, mode: "points" as const, tone: "ink3" as const, size: 3 }] : []),
      { name: "OTM PUT", y: put, mode: "points", tone: "ink", size: 4, lo: putLo, hi: putHi },
      { name: "OTM CALL", y: call, mode: "points", tone: "ink2", size: 4, lo: callLo, hi: callHi },
      { name: "SVI", y: svi, tone: "ink", width: 1.5, span: true },
    ];
    return { x: xs, series: out };
  }, [c, axis, showAll]);
  const fwd = axis === "strike" ? F : 0;
  const spot = axis === "strike" ? c.spot : Math.log(c.spot / F);
  const bfly = fit?.butterfly;
  return (
    <div className="vx-smile">
      <div className="vx-smile-main">
        <div className="vx-toolbar">
          <SegmentedControl size="sm" ariaLabel="Smile x-axis" options={[{ value: "strike", label: "STRIKE" }, { value: "k", label: "LN(K/F)" }]} value={axis} onChange={setAxis} />
          <Toggle label="ITM · FILTERED" checked={showAll} onChange={setShowAll} />
        </div>
        <XYChart
          x={x}
          series={series}
          xFormat="num"
          digits={axis === "strike" ? 2 : 3}
          yFormat="pct"
          height={380}
          xTitle={axis === "strike" ? "Strike" : "k = ln(K/F)"}
          vlines={[{ at: fwd, label: "FWD", tone: "ink2", dash: "dot" }, ...(Math.abs(spot - fwd) > 1e-9 ? [{ at: spot, label: "SPOT", tone: "ink3" as const, dash: "dash" as const }] : [])]}
          ariaLabel={`Implied volatility smile, ${c.underlying} ${c.slice.expiry}`}
        />
      </div>
      <aside className="vx-smile-side">
        <div className="vx-side-title">Fit</div>
        {fit ? (
          <>
            <KV
              rows={[
                { label: "Butterfly", value: <Check ok={bfly?.arbitrage_free} />, info: INFO.butterfly_g },
                { label: "RMSE · pts", value: fmtNum(fit.rmse_vol_pts, 3), tone: fit.rmse_vol_pts >= 1 ? "vx-strong" : "" },
                { label: "Max |err| · pts", value: fmtNum(fit.max_abs_err_vol_pts, 3) },
                { label: "N quotes", value: fmtNum(fit.n, 0) },
                { label: <span className="vx-sym">min g(k)</span>, value: fmtNum(bfly?.g_min, 4), info: INFO.butterfly_g, tone: bfly?.arbitrage_free === false ? "loss" : "" },
                { label: "Vendor gap · MAD", value: c.slice.vendor_iv_mad != null ? `${fmtNum(c.slice.vendor_iv_mad * 100, 2)} PTS` : "—", info: INFO.vendor_iv },
              ]}
            />
            <div className="vx-side-title">SVI · raw</div>
            <KV
              cols={2}
              rows={[
                { label: <span className="vx-sym">a</span>, value: fmtNum(fit.params.a, 5) },
                { label: <span className="vx-sym">b</span>, value: fmtNum(fit.params.b, 4) },
                { label: <span className="vx-sym">ρ</span>, value: fmtNum(fit.params.rho, 3) },
                { label: <span className="vx-sym">m</span>, value: fmtNum(fit.params.m, 4) },
                { label: <span className="vx-sym">σ</span>, value: fmtNum(fit.params.sigma, 4) },
                { label: "Wings L / R", value: `${fmtNum(fit.wing_slopes[0], 3)} / ${fmtNum(fit.wing_slopes[1], 3)}`, info: { title: "Wing slopes", text: "b(1 − ρ) and b(1 + ρ); Lee's moment formula needs both ≤ 2.", reference: "Lee (2004), Math. Finance 14(3)" } },
              ]}
            />
          </>
        ) : (
          <div className="vx-none num">NO SVI FIT · SEE NOTES</div>
        )}
      </aside>
    </div>
  );
}

// ------------------------------------------------------------------ term structure & skew
function TermChart({ d }: { d: Surface }) {
  const rows = d.term_structure;
  const x = rows.map((r) => r.dte);
  const series: XYSeries[] = [
    { name: "ATM", y: rows.map((r) => r.atm_iv ?? null), tone: "ink", width: 1.5, span: true },
    { name: "MODEL-FREE", y: rows.map((r) => r.mf_vol ?? null), tone: "ink2", dash: "dash", span: true },
    { name: "ATM · QUOTES", y: rows.map((r) => r.atm_iv_market ?? null), mode: "points", tone: "ink3", size: 4 },
  ];
  return (
    <>
      <Readline
        items={[
          { k: "ATM 30D", v: fmtPct(d.atm_30d?.iv, 1) },
          { k: "MODEL-FREE 30D", v: fmtPct(d.vix_style_30d ? d.vix_style_30d.index / 100 : null, 1) },
        ]}
      />
      <XYChart x={x} series={series} logX xTicks={dayTicks(x)} yFormat="pct" digits={1} height={280} xTitle="Days to expiry" ariaLabel="Implied volatility term structure" />
    </>
  );
}

function SkewChart({ rows }: { rows: TermRow[] }) {
  const x = rows.map((r) => r.dte);
  const col = (k: keyof TermRow) => rows.map((r) => (r[k] as number | null | undefined) ?? null);
  const series: XYSeries[] = [
    { name: "RR 25Δ", y: col("rr_25d"), tone: "ink", span: true },
    { name: "RR 10Δ", y: col("rr_10d"), tone: "ink", dash: "dot", span: true },
    { name: "BF 25Δ", y: col("bf_25d"), tone: "ink2", span: true },
    { name: "BF 10Δ", y: col("bf_10d"), tone: "ink2", dash: "dot", span: true },
  ];
  return <XYChart x={x} series={series} logX xTicks={dayTicks(x)} yFormat="pct" digits={2} height={308} hlines={[{ at: 0, label: "0", tone: "ink2", dash: "solid" }]} xTitle="Days to expiry" ariaLabel="Risk reversals and butterflies by expiry" />;
}

// ------------------------------------------------------------------ arbitrage
function ArbPanel({ d }: { d: Surface }) {
  const smiles = d.smiles;
  const nbOk = smiles.filter((s) => s.fit.butterfly.arbitrage_free).length;
  const ncOk = d.calendar.filter((c) => !c.violation).length;
  type BRow = { slice: string; dte: number; n: number; rmse: number; g: number | null; kg: number | null; ok: boolean };
  const brows: BRow[] = smiles.map((s) => ({ slice: s.slice, dte: s.dte, n: s.fit.n, rmse: s.fit.rmse_vol_pts, g: s.fit.butterfly.g_min, kg: s.fit.butterfly.k_at_g_min, ok: s.fit.butterfly.arbitrage_free }));
  const bcols: Column<BRow>[] = [
    { key: "ok", label: "Butterfly", sortable: false, render: (r) => <Check ok={r.ok} /> },
    { key: "slice", label: "Expiry", render: (r) => <span className="num">{r.slice}</span> },
    { key: "dte", label: "Days", numeric: true, format: fmtDays },
    { key: "n", label: "N", numeric: true, format: (v) => fmtNum(v, 0), hideBelow: 600 },
    { key: "rmse", label: "RMSE · pts", numeric: true, format: (v) => fmtNum(v, 3) },
    { key: "g", label: <span className="vx-sym">min g(k)</span>, numeric: true, format: (v) => fmtNum(v, 4), info: INFO.butterfly_g, color: (v) => (v != null && v < 0 ? "loss" : undefined) },
    { key: "kg", label: <span className="vx-sym">at k</span>, numeric: true, format: (v) => fmtNum(v, 3), hideBelow: 900 },
  ];
  const Tlabel = (T: number) => smiles.find((x) => Math.abs(x.T - T) < 1e-9)?.slice ?? fmtDays(T * 365);
  const ccols: Column<CalendarCheck>[] = [
    { key: "violation", label: "Calendar", sortable: false, render: (c) => <Check ok={!c.violation} /> },
    { key: "T1", label: "Pair", render: (c) => <span className="num">{Tlabel(c.T1)} → {Tlabel(c.T2)}</span> },
    { key: "min_dw", label: <span className="vx-sym">min Δw</span>, numeric: true, format: (v) => fmtSci(v, 2), info: INFO.calendar, color: (v) => (v != null && v < 0 ? "loss" : undefined) },
    { key: "k_at_min", label: <span className="vx-sym">at k</span>, numeric: true, format: (v) => fmtNum(v, 3), hideBelow: 900 },
  ];
  return (
    <div className="vx-arb">
      <Readline
        items={[
          { k: "BUTTERFLY", v: `${nbOk}/${smiles.length}`, tone: nbOk < smiles.length ? "loss" : "" },
          { k: "CALENDAR", v: `${ncOk}/${d.calendar.length}`, tone: ncOk < d.calendar.length ? "loss" : "" },
        ]}
      />
      <div className="vx-arb-grid">
        <div>
          <h4 className="vx-table-title">Across strikes · per expiry</h4>
          <DataTable columns={bcols} rows={brows} rowKey={(r) => r.slice} />
        </div>
        <div>
          <h4 className="vx-table-title">Across maturities · adjacent pairs</h4>
          <DataTable columns={ccols} rows={d.calendar} rowKey={(c) => `${c.T1}-${c.T2}`} />
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ parity
function ParityTable({ rows, spot }: { rows: SliceSummary[]; spot: number }) {
  const cols: Column<SliceSummary>[] = [
    { key: "slice", label: "Expiry", render: (r) => <span className="num">{r.slice}{r.settlement === "AM" ? " · AM" : ""}</span> },
    { key: "dte", label: "Days", numeric: true, format: fmtDays },
    { key: "forward", label: <span className="vx-sym">F</span>, numeric: true, format: (v) => fmtNum(v, 2) },
    { key: "carry", label: <span className="vx-sym">F/S − 1</span>, numeric: true, value: (r) => r.forward / spot - 1, format: (v) => fmtSignedPct(v, 2), hideBelow: 900 },
    { key: "discount", label: <span className="vx-sym">D</span>, numeric: true, format: (v) => fmtNum(v, 5), hideBelow: 1200 },
    { key: "rate", label: <span className="vx-sym">r</span>, numeric: true, format: (v) => fmtPct(v, 2), info: { title: "Implied rate", text: "Continuously compounded, from the parity discount factor.", formula: "r = -\\ln D / T" } },
    { key: "div_yield", label: <span className="vx-sym">q</span>, numeric: true, format: (v) => fmtPct(v, 2), info: { title: "Implied yield", text: "Continuous dividend (or borrow) yield implied by the forward.", formula: "q = r - \\ln(F/S)/T" } },
    { key: "rate_source", label: <span className="vx-sym">r from</span>, render: (r) => <span className="num">{r.rate_source === "regression" ? "OWN FIT" : "NEIGHBOURS"}</span> },
    { key: "rate_se", label: <span className="vx-sym">SE(r)</span>, numeric: true, format: (v) => fmtPct(v, 2), hideBelow: 1200 },
    { key: "parity_rmse", label: "RMSE", numeric: true, format: (v) => fmtNum(v, 3), hideBelow: 900 },
    { key: "n_valid", label: "Valid / listed", numeric: true, render: (r) => <span className="num">{r.n_valid} / {r.n_contracts}</span>, hideBelow: 600 },
  ];
  return <DataTable columns={cols} rows={rows} rowKey={(r) => r.slice} maxHeight={440} />;
}
