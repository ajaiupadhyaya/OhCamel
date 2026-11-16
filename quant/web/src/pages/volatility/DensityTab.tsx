/**
 * DENSITY (GET /api/options/density/{t}?expiry=): the Breeden–Litzenberger density from the
 * SVI smile against a lognormal at the ATM vol, implied probabilities, quantiles, moments and
 * the sanity checks. The straddle-implied move comes from the surface (GET /api/options/surface).
 */
import { useMemo, useState, type CSSProperties } from "react";
import { DataTable, Panel, SegmentedControl, StatGrid, StatTile, type Column } from "../../components";
import { XYChart } from "../../charts/XYChart";
import { Note } from "../../design";
import { fmtDate, fmtNum, fmtPct, fmtSci, fmtSignedPct } from "../../lib/format";
import { INFO } from "./info";
import { Check, KV, Readline, expiryLabel, type useDensity, type useSurface } from "./shared";
import type { Density } from "./types";

export function DensityTab({ q, surface }: { q: ReturnType<typeof useDensity>; surface: ReturnType<typeof useSurface> }) {
  const d = q.data;
  const row = d ? surface.data?.term_structure.find((r) => r.slice === d.expiry) : undefined;
  const asOf = d?.as_of;
  return (
    <>
      <Panel<Density>
        title={
          <>
            {d ? `Density · ${expiryLabel({ expiry: d.expiry, dte: d.dte })}` : "Density"}
            <Note n={1} to="density" />
          </>
        }
        query={q}
        notes={[]}
        skeletonHeight={100}
        asOf={asOf}
      >
        {(x) => (
          <>
            <Readline
              items={[
                { k: "SPOT", v: fmtNum(x.spot, 2) },
                ...(x.iv_spot_source === "implied" ? [{ k: "IV SPOT", v: `${fmtNum(x.iv_spot, 2)} · OPTION-IMPLIED` }] : []),
                { k: "FWD", v: fmtNum(x.forward, 2) },
                { k: "T", v: `${fmtNum(x.T, 4)}Y` },
              ]}
            />
            <StatGrid min={150}>
              <StatTile label="Implied move" value={row?.implied_move} format={(v) => `±${fmtPct(v, 1)}`} info={INFO.implied_move} caption={row?.straddle != null ? `STRADDLE ${fmtNum(row.straddle, 2)}` : "ATM STRADDLE"} />
              <StatTile label="1σ · Q" value={x.moments.std} format={(v) => `±${fmtPct(v, 1)}`} info={{ title: "Risk-neutral s.d.", text: "Standard deviation of S_T / F − 1 under the market density." }} caption={`LOGNORMAL ±${fmtPct(x.lognormal_moments.std, 1)}`} />
              <StatTile label="ATM vol" value={x.atm_iv} format={(v) => fmtPct(v, 1)} info={INFO.atm_iv} />
              <StatTile label="Skew" value={x.moments.skew} format={(v) => fmtNum(v, 2, { signed: true })} info={INFO.skew} caption={`LOGNORMAL ${fmtNum(x.lognormal_moments.skew, 2, { signed: true })}`} />
              <StatTile label="Excess kurt" value={x.moments.excess_kurtosis} format={(v) => fmtNum(v, 2)} info={INFO.kurt} caption={`LOGNORMAL ${fmtNum(x.lognormal_moments.excess_kurtosis, 2)}`} />
            </StatGrid>
          </>
        )}
      </Panel>

      <div className="grid-3">
        <Panel<Density> title="Q density · per 1% move" info={INFO.rnd} query={q} span={2} skeletonHeight={400} asOf={asOf}>
          {(x) => <DensityChart d={x} />}
        </Panel>
        <Panel<Density> title="Checks" query={q} notes={[]} skeletonHeight={400} asOf={asOf}>
          {(x) => <Checks d={x} />}
        </Panel>
      </div>

      <div className="grid-2">
        <Panel<Density> title="P(move) · Q" info={INFO.q_prob} query={q} flush notes={["Moves from spot, not the forward. Risk-neutral odds price insurance; they are not real-world frequencies."]} skeletonHeight={320} asOf={asOf}>
          {(x) => <ProbTable d={x} />}
        </Panel>
        <Panel<Density> title="Quantiles · Q" info={{ title: "Risk-neutral quantiles", text: "The level K with P(S_T ≤ K) = p, read off the risk-neutral CDF.", formula: "K_p = F_Q^{-1}(p)", reference: "Breeden & Litzenberger (1978)" }} query={q} flush notes={[]} skeletonHeight={320} asOf={asOf}>
          {(x) => <QuantileTable d={x} />}
        </Panel>
      </div>
    </>
  );
}

function DensityChart({ d }: { d: Density }) {
  const [axis, setAxis] = useState<"price" | "ret">("price");
  const view = useMemo(() => {
    const idx: number[] = [];
    d.cdf.forEach((c, i) => {
      if (c != null && c >= 0.0005 && c <= 0.9995) idx.push(i);
    });
    const pick = <T,>(a: T[]) => idx.map((i) => a[i]);
    const K = pick(d.strikes);
    // density is per $ of strike; shown as probability per 1% move from spot: q(K)·S·0.01
    const scale = d.iv_spot * 0.01;
    return {
      x: axis === "price" ? K : K.map((k) => k / d.iv_spot - 1),
      q: pick(d.density).map((v) => (v == null ? null : v * scale)),
      ln: pick(d.lognormal_density).map((v) => (v == null ? null : v * scale)),
    };
  }, [d, axis]);
  const X = (level: number) => (axis === "price" ? level : level / d.iv_spot - 1);
  const q05 = d.quantiles.find((x) => Math.abs(x.p - 0.05) < 1e-9);
  const q95 = d.quantiles.find((x) => Math.abs(x.p - 0.95) < 1e-9);
  return (
    <>
      <div className="vx-toolbar">
        <SegmentedControl size="sm" ariaLabel="Density x-axis" options={[{ value: "price", label: "PRICE" }, { value: "ret", label: "% FROM SPOT" }]} value={axis} onChange={setAxis} />
      </div>
      <XYChart
        x={view.x}
        series={[
          { name: "Q", y: view.q, tone: "ink", width: 1.75 },
          { name: `LN ${fmtPct(d.atm_iv, 1)}`, y: view.ln, tone: "ink3", dash: "dash" },
        ]}
        xFormat={axis === "price" ? "num" : "pct"}
        digits={axis === "price" ? 2 : 1}
        yFormat="pct"
        zero
        height={360}
        xTitle={axis === "price" ? `${d.underlying} at expiry` : "Return from spot"}
        vlines={[
          { at: X(d.iv_spot), label: d.iv_spot_source === "implied" ? "SPOT (IMPLIED)" : "SPOT", tone: "ink2", dash: "dot" },
          ...(q05 ? [{ at: X(q05.level), label: "P5", tone: "ink3" as const, dash: "dot" as const }] : []),
          ...(q95 ? [{ at: X(q95.level), label: "P95", tone: "ink3" as const, dash: "dot" as const }] : []),
        ]}
        ariaLabel={`Risk-neutral density of ${d.underlying} at ${d.expiry}`}
      />
    </>
  );
}

/** Tail masses above this are shown as their own figures. */
export const TAIL_SHOWN = 0.001;

/** Whether to show the two tail masses as their own rows. */
export function showTails(c: Density["checks"]): boolean {
  return c.tail_mass_left > TAIL_SHOWN || c.tail_mass_right > TAIL_SHOWN;
}

/**
 * The marks (PASS / FAIL) are the API's `checks.passed`, the checks that can fail: negative mass
 * (butterfly arbitrage), the mean against the forward (martingale), and a numerical cross-check of
 * the finite-difference density against the smile's closed form (a broken stencil or an unresolved
 * grid; it cannot detect arbitrage). Grid integral + tail masses and the tail masses themselves are
 * plain figures: the sum telescopes to 1 for any smile, and a bad tail has already failed the API.
 */
export function Checks({ d }: { d: Density }) {
  const c = d.checks;
  const ok = c.passed;
  const tails = showTails(c)
    ? [
        { label: "Mass below grid", value: fmtPct(c.tail_mass_left, 2), info: { text: "Probability that the expiry price is below the lowest strike of the grid (the grid stops at ln(K/F) = −3)." } },
        { label: "Mass above grid", value: fmtPct(c.tail_mass_right, 2), info: { text: "Probability that the expiry price is above the highest strike of the grid (the grid stops at ln(K/F) = +3)." } },
      ]
    : [{ label: "Mass beyond grid", value: fmtSci(c.tail_mass_left + c.tail_mass_right, 2), info: { text: "Probability outside the strike grid (ln(K/F) in [−3, 3]), both ends together." } }];
  return (
    <KV
      rows={[
        { label: "Numerical cross-check", value: <>{fmtNum(c.integral, 5)} <Check ok={ok.analytic} /></>, hint: `CLOSED FORM ${fmtNum(c.analytic_integral, 5)}`, info: { text: "The finite-difference density's probability on the strike grid against the same integral of the smile's closed-form density (Gatheral & Jacquier 2014); they must agree to 0.001. It catches a broken difference stencil or a grid too coarse for the smile. It cannot detect arbitrage: both are the same function of the same smile.", formula: "\\int q_{FD}(K)\\,dK \\;\\text{vs}\\; \\int q_{closed}(K)\\,dK" } },
        ...tails,
        { label: <span className="vx-sym">∫ q dK + tails</span>, value: fmtNum(c.integral_plus_tails, 5), info: { text: "Grid probability plus the mass beyond the grid. Information, not a check: it adds to 1 for any smile, arbitrage or not." } },
        { label: <>MEAN − <span className="vx-sym">F</span></>, value: <>{fmtNum(c.mean_minus_forward, 4)} <Check ok={ok.mean} /></>, hint: `${fmtSignedPct(c.mean_minus_forward / d.forward, 4)} OF F`, info: { text: "Martingale condition: the expected expiry price equals the forward.", formula: "\\mathbb E_Q[S_T] = F" } },
        { label: "Negative mass", value: <>{fmtSci(c.negative_mass, 2)} <Check ok={ok.negative_mass} /></>, info: { text: "Probability mass below zero: possible only if the smile admits butterfly arbitrage." } },
        { label: "Smile butterfly", value: <Check ok={c.butterfly.arbitrage_free} />, info: INFO.butterfly_g },
        { label: <span className="vx-sym">min g(k)</span>, value: fmtNum(c.butterfly.g_min, 4), info: INFO.butterfly_g },
        { label: <span className="vx-sym">D</span>, value: fmtNum(d.discount, 5), info: { title: "Discount factor", text: "Parity-implied for this expiry; the density is the second strike-derivative of calls over D." } },
        { label: "Expiry", value: `${fmtDate(d.expiry).toUpperCase()} · ${fmtNum(d.dte, 1)}D` },
      ]}
    />
  );
}

type ProbRow = { move: number; down: number | null; up: number | null; two: number | null; ln: number | null };

function ProbTable({ d }: { d: Density }) {
  const rows = useMemo<ProbRow[]>(() => {
    const at = (m: number) => d.prob_below.find((p) => Math.abs(p.moneyness - m) < 1e-6)?.p ?? null;
    return [0.025, 0.05, 0.1, 0.15, 0.2, 0.3].map((m) => {
      const upBelow = at(1 + m);
      const pm = d.prob_move.find((p) => Math.abs(p.move - m) < 1e-9);
      return { move: m, down: at(1 - m), up: upBelow == null ? null : 1 - upBelow, two: pm?.p ?? null, ln: pm?.p_lognormal ?? null };
    });
  }, [d]);
  const bar = (v: number | null, down: boolean) => (
    <span className="vx-probcell">
      <span className={`vx-probbar ${down ? "vx-probbar-down" : ""}`} style={{ "--w": `${Math.round((v ?? 0) * 100)}%` } as CSSProperties} aria-hidden />
      <span className="num">{fmtPct(v, 1)}</span>
    </span>
  );
  const cols: Column<ProbRow>[] = [
    { key: "move", label: "Move", render: (r) => <span className="num">±{fmtPct(r.move, r.move < 0.05 ? 1 : 0)}</span> },
    { key: "down", label: "P(fall >)", numeric: true, render: (r) => bar(r.down, true), info: { ...INFO.q_prob, title: "P(fall more than x)", formula: "P_Q\\left(S_T < S_0(1 - x)\\right)" } },
    { key: "up", label: "P(rise >)", numeric: true, render: (r) => bar(r.up, false), info: { ...INFO.q_prob, title: "P(rise more than x)", formula: "P_Q\\left(S_T > S_0(1 + x)\\right)" } },
    { key: "two", label: "Either", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 600 },
    { key: "ln", label: "LN either", numeric: true, format: (v) => fmtPct(v, 1), info: { ...INFO.lognormal, title: "Lognormal, either way" }, hideBelow: 900 },
  ];
  return <DataTable columns={cols} rows={rows} rowKey={(r) => String(r.move)} />;
}

function QuantileTable({ d }: { d: Density }) {
  const cols: Column<Density["quantiles"][number]>[] = [
    { key: "p", label: "P", render: (r) => <span className="num">{fmtPct(r.p, 0)}</span> },
    { key: "level", label: "Level", numeric: true, format: (v) => fmtNum(v, 2) },
    { key: "return", label: "From spot", numeric: true, format: (v) => fmtSignedPct(v, 1), color: (v) => (typeof v === "number" && v < 0 ? "loss" : undefined) },
  ];
  return <DataTable columns={cols} rows={d.quantiles} rowKey={(r) => String(r.p)} isActive={(r) => Math.abs(r.p - 0.5) < 1e-9} />;
}
