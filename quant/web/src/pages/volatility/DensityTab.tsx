/**
 * Risk-neutral density of one expiry (GET /api/options/density/{t}?expiry=): the
 * Breeden–Litzenberger density from the SVI smile against a lognormal at ATM vol, implied
 * probabilities of moves, quantiles, moments and sanity checks. The straddle-implied move
 * comes from the surface payload (GET /api/options/surface/{t}).
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, Panel, SegmentedControl, StatGrid, StatTile, withAlpha, type Column } from "../../components";
import { fmtDate, fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import type { Tokens } from "../../lib/theme";
import { INFO } from "./info";
import { Check, KV, Swatch, expiryLabel, type useDensity, type useSurface } from "./shared";
import type { Density } from "./types";

export function DensityTab({ q, surface }: { q: ReturnType<typeof useDensity>; surface: ReturnType<typeof useSurface> }) {
  const d = q.data;
  const row = d ? surface.data?.term_structure.find((r) => r.slice === d.expiry) : undefined;
  return (
    <>
      <Panel<Density>
        title="What the options say about the price at expiry"
        subtitle={d ? `${d.underlying} on ${expiryLabel({ expiry: d.expiry, dte: d.dte })} · forward ${fmtNum(d.forward, 2)} · spot ${fmtNum(d.spot, 2)}` : undefined}
        info={INFO.rnd}
        query={q}
        notes={[]}
        skeletonHeight={100}
      >
        {(x) => (
          <StatGrid min={150}>
            <StatTile label="Implied move" value={row?.implied_move} format={(v) => `±${fmtPct(v, 1)}`} info={INFO.implied_move} caption={row?.straddle != null ? `ATM straddle ${fmtNum(row.straddle, 2)}` : "from the ATM straddle"} />
            <StatTile label="1σ under Q" value={x.moments.std} format={(v) => `±${fmtPct(v, 1)}`} info={{ title: "Risk-neutral standard deviation", text: "Standard deviation of S_T / F − 1 under the market density: a one-standard-deviation move to expiry." }} caption={`lognormal ±${fmtPct(x.lognormal_moments.std, 1)}`} />
            <StatTile label="ATM vol" value={x.atm_iv} format={(v) => fmtPct(v, 1)} info={INFO.atm_iv} />
            <StatTile label="Skewness" value={x.moments.skew} format={(v) => fmtNum(v, 2, { signed: true })} tone="auto" info={INFO.skew} caption={`lognormal ${fmtNum(x.lognormal_moments.skew, 2, { signed: true })}`} />
            <StatTile label="Excess kurtosis" value={x.moments.excess_kurtosis} format={(v) => fmtNum(v, 2)} info={INFO.kurt} caption={`lognormal ${fmtNum(x.lognormal_moments.excess_kurtosis, 2)}`} />
          </StatGrid>
        )}
      </Panel>

      <div className="grid-3">
        <Panel<Density>
          title="Risk-neutral density"
          subtitle="The distribution of the expiry price that reprices every option in the chain, against the bell curve Black–Scholes would assume at the ATM vol. Shaded: the central 90%."
          info={INFO.rnd}
          query={q}
          span={2}
          skeletonHeight={400}
        >
          {(x) => <DensityChart d={x} />}
        </Panel>
        <Panel<Density> title="Checks" subtitle="A valid density integrates to one and is centred on the forward." info={{ text: "Numerical sanity checks on the extracted density. Failures usually mean the smile has butterfly arbitrage or the strike grid misses tail mass." }} query={q} notes={[]} skeletonHeight={400}>
          {(x) => <Checks d={x} />}
        </Panel>
      </div>

      <div className="grid-2">
        <Panel<Density> title="Implied probabilities" subtitle="Chance, under the pricing measure, that the price finishes beyond each move from today's spot." info={INFO.q_prob} query={q} flush notes={[]} skeletonHeight={320}>
          {(x) => <ProbTable d={x} />}
        </Panel>
        <Panel<Density> title="Quantiles" subtitle="Price levels the market assigns a given chance of finishing below." info={{ title: "Risk-neutral quantiles", text: "The level K with P(S_T ≤ K) = p, read off the risk-neutral CDF.", formula: "K_p = F_Q^{-1}(p)", reference: "Breeden & Litzenberger (1978)" }} query={q} flush notes={[]} skeletonHeight={320}>
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
    // density is per $ of strike; per unit of return it is q(K)·S
    const scale = axis === "ret" ? d.spot : 1;
    return {
      x: axis === "price" ? K : K.map((k) => k / d.spot - 1),
      q: pick(d.density).map((v) => (v == null ? null : v * scale)),
      ln: pick(d.lognormal_density).map((v) => (v == null ? null : v * scale)),
      cdf: pick(d.cdf),
      ret: K.map((k) => k / d.spot - 1),
      K,
    };
  }, [d, axis]);
  const X = (level: number) => (axis === "price" ? level : level / d.spot - 1);
  const q05 = d.quantiles.find((x) => Math.abs(x.p - 0.05) < 1e-9);
  const q95 = d.quantiles.find((x) => Math.abs(x.p - 0.95) < 1e-9);
  const data = useMemo(
    () => (t: Tokens): Data[] => [
      { type: "scatter", mode: "lines", name: "Lognormal @ ATM vol", x: view.x, y: view.ln, line: { color: t.text3, width: 1.4, dash: "dash" }, hoverinfo: "skip" },
      {
        type: "scatter",
        mode: "lines",
        name: "Market (risk-neutral)",
        x: view.x,
        y: view.q,
        fill: "tozeroy",
        fillcolor: withAlpha(t.categorical[0], 0.14),
        line: { color: t.categorical[0], width: 2.2 },
        customdata: view.K.map((k, i) => [k, view.ret[i], view.cdf[i], view.ln[i]]),
        hovertemplate: "<b>%{customdata[0]:,.2f}</b> (%{customdata[1]:+.1%})<br>P(below) %{customdata[2]:.1%}<extra></extra>",
      },
    ] as Data[],
    [view],
  );
  const layout = useMemo(
    () => (t: Tokens) => ({
      hovermode: "x",
      showlegend: false,
      xaxis: { title: { text: axis === "price" ? `${d.underlying} at expiry` : "Return from spot" }, tickformat: axis === "price" ? ",.0f" : "+.0%" },
      yaxis: { showticklabels: false, ticks: "", title: { text: "" }, rangemode: "tozero", showgrid: false, zeroline: false },
      shapes: [
        ...(q05 && q95 ? [{ type: "rect", xref: "x", yref: "paper", x0: X(q05.level), x1: X(q95.level), y0: 0, y1: 1, fillcolor: withAlpha(t.categorical[0], 0.05), line: { width: 0 }, layer: "below" }] : []),
        { type: "line", yref: "paper", y0: 0, y1: 1, x0: X(d.spot), x1: X(d.spot), line: { color: t.text2, width: 1, dash: "dot" } },
        { type: "line", yref: "paper", y0: 0, y1: 0.9, x0: X(d.forward), x1: X(d.forward), line: { color: t.ruleStrong, width: 1 } },
      ],
      annotations: [
        { x: X(d.spot), yref: "paper", y: 1, text: `spot ${fmtNum(d.spot, 2)}`, showarrow: false, xanchor: "left", yanchor: "top", xshift: 4, font: { size: 10, color: t.text2 } },
        ...(q05 ? [{ x: X(q05.level), yref: "paper", y: 0.05, text: "5%", showarrow: false, xanchor: "left", xshift: 3, font: { size: 10, color: t.text3 } }] : []),
        ...(q95 ? [{ x: X(q95.level), yref: "paper", y: 0.05, text: "95%", showarrow: false, xanchor: "right", xshift: -3, font: { size: 10, color: t.text3 } }] : []),
      ],
      margin: { l: 16, r: 8, t: 36, b: 44 },
    }),
    [axis, d, q05, q95], // eslint-disable-line react-hooks/exhaustive-deps
  );
  return (
    <>
      <div className="vx-toolbar">
        <SegmentedControl size="sm" ariaLabel="Density x-axis" options={[{ value: "price", label: "Price" }, { value: "ret", label: "% from spot" }]} value={axis} onChange={setAxis} />
        <span className="vx-legend small">
          <span><Swatch color="var(--c1)" /> Market density</span>
          <span><Swatch color="var(--text-3)" dash /> Lognormal at ATM vol {fmtPct(d.atm_iv, 1)}</span>
        </span>
      </div>
      <Chart data={data} layout={layout as never} height={380} />
    </>
  );
}

function Checks({ d }: { d: Density }) {
  const c = d.checks;
  const intOk = Math.abs(c.integral - 1) <= 0.01;
  const meanOk = Math.abs(c.mean_minus_forward / d.forward) <= 0.005;
  const negOk = c.negative_mass <= 1e-4;
  return (
    <div className="stack">
      <div className="row-wrap">
        <Check ok={intOk} label="Integrates to 1" />
        <Check ok={meanOk} label="Mean = forward" />
        <Check ok={negOk} label="Non-negative" />
        <Check ok={c.butterfly.arbitrage_free} label="Smile butterfly-free" />
      </div>
      <KV
        rows={[
          { label: "∫ q(K) dK", value: fmtNum(c.integral, 5), info: { text: "Total probability captured on the strike grid; should be 1.", formula: "\\int q(K)\\,dK" }, tone: intOk ? "" : "warn" },
          { label: "Mean − forward", value: fmtNum(c.mean_minus_forward, 4), hint: `${fmtSignedPct(c.mean_minus_forward / d.forward, 4)} of F`, info: { text: "Under the pricing measure the expected expiry price equals the forward (martingale condition).", formula: "\\mathbb E_Q[S_T] = F" }, tone: meanOk ? "" : "warn" },
          { label: "Negative mass", value: c.negative_mass === 0 ? "0" : c.negative_mass.toExponential(2), info: { text: "Probability mass below zero — only possible if the smile admits butterfly arbitrage." }, tone: negOk ? "" : "loss" },
          { label: "min g(k)", value: fmtNum(c.butterfly.g_min, 4), info: INFO.butterfly_g },
          { label: "Discount factor", value: fmtNum(d.discount, 5), info: { text: "Parity-implied discount factor for this expiry; the density is the second strike-derivative of calls divided by it." } },
          { label: "Time to expiry", value: `${fmtNum(d.T, 4)} y`, hint: `${fmtNum(d.dte, 1)} calendar days to ${fmtDate(d.expiry)}` },
        ]}
      />
    </div>
  );
}

type ProbRow = { move: number; down: number | null; up: number | null; two: number | null; ln: number | null };

function ProbTable({ d }: { d: Density }) {
  const rows = useMemo<ProbRow[]>(() => {
    const at = (m: number) => d.prob_below.find((p) => Math.abs(p.moneyness - m) < 1e-6)?.p ?? null;
    const moves = [0.025, 0.05, 0.1, 0.15, 0.2, 0.3];
    return moves.map((m) => {
      const down = at(1 - m);
      const upBelow = at(1 + m);
      const pm = d.prob_move.find((p) => Math.abs(p.move - m) < 1e-9);
      return { move: m, down, up: upBelow == null ? null : 1 - upBelow, two: pm?.p ?? null, ln: pm?.p_lognormal ?? null };
    });
  }, [d]);
  const bar = (v: number | null, tone: "loss" | "gain") => (
    <span className="vx-probcell">
      <span className={`vx-probbar ${tone}`} style={{ width: `${Math.round((v ?? 0) * 100)}%` }} aria-hidden />
      <span className="num">{fmtPct(v, 1)}</span>
    </span>
  );
  const cols: Column<ProbRow>[] = [
    { key: "move", label: "Move", render: (r) => <span className="num">{fmtPct(r.move, r.move < 0.05 ? 1 : 0)}</span> },
    { key: "down", label: "P(fall more)", numeric: true, render: (r) => bar(r.down, "loss"), info: { ...INFO.q_prob, title: "P(fall more than x)", formula: "P_Q\\left(S_T < S_0(1 - x)\\right)" } },
    { key: "up", label: "P(rise more)", numeric: true, render: (r) => bar(r.up, "gain"), info: { ...INFO.q_prob, title: "P(rise more than x)", formula: "P_Q\\left(S_T > S_0(1 + x)\\right)" } },
    { key: "two", label: "Either", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 600 },
    { key: "ln", label: "Lognorm.", numeric: true, format: (v) => fmtPct(v, 1), info: { ...INFO.lognormal, title: "Lognormal, either way" }, hideBelow: 900 },
  ];
  return (
    <>
      <DataTable className="vx-tight" columns={cols} rows={rows} rowKey={(r) => String(r.move)} />
      <p className="vx-foot-note subtle small">Moves are measured from spot ({fmtNum(d.spot, 2)}), not the forward. Risk-neutral odds overstate crashes relative to real-world frequencies — they are what insurance costs.</p>
    </>
  );
}

function QuantileTable({ d }: { d: Density }) {
  const cols: Column<Density["quantiles"][number]>[] = [
    { key: "p", label: "Probability", render: (r) => <span className="num">{fmtPct(r.p, 0)}</span> },
    { key: "level", label: "Level", numeric: true, format: (v) => fmtNum(v, 2) },
    { key: "return", label: "From spot", numeric: true, format: (v) => fmtSignedPct(v, 1), color: "sign" },
  ];
  return <DataTable columns={cols} rows={d.quantiles} rowKey={(r) => String(r.p)} isActive={(r) => Math.abs(r.p - 0.5) < 1e-9} />;
}
