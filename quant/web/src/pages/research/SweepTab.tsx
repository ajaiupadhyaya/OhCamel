/**
 * Sweep & overfitting tab — POST /api/backtest/sweep: Sharpe over a parameter grid, then the
 * three guardrails: CSCV probability of backtest overfitting (with the logit histogram and
 * the performance-degradation scatter), the deflated Sharpe ratio and Hansen's SPA test.
 */
import { useEffect, useMemo, useState } from "react";
import type { Data } from "plotly.js";
import {
  Chart,
  DataTable,
  Field,
  HeatmapChart,
  Panel,
  SegmentedControl,
  type Column,
} from "../../components";
import { Icon } from "../../components/Icon";
import { fmtDate, fmtMultiple, fmtPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import { fmtParams, humanize, sweepableParams, type BaseBody } from "./config";
import { GridEditor, defaultAxes, gridFromAxes, type Axis } from "./GridEditor";
import { INFO } from "./info";
import {
  Bars,
  BinnedHistogram,
  Verdict,
  finite,
  fmtProb,
  fmtSR,
  label,
} from "./shared";
import type { StrategySpec, SweepOut } from "./types";

export function SweepTab({
  spec,
  base,
  maxCombos,
  onData,
}: {
  spec: StrategySpec;
  base: BaseBody | null;
  maxCombos: number;
  onData?: (d: SweepOut | undefined) => void;
}) {
  const params = useMemo(() => sweepableParams(spec), [spec]);
  const [axes, setAxes] = useState<Axis[]>(() => defaultAxes(params, 2));
  const [partitions, setPartitions] = useState("16");
  const draft = gridFromAxes(axes, params);
  const [submitted, setSubmitted] = useState(() => ({
    grid: draft.grid,
    n_partitions: 16,
  }));
  const body =
    base && params.length && Object.keys(submitted.grid).length
      ? { ...base, ...submitted }
      : null;
  const q = useApiPost<SweepOut>("/backtest/sweep", body, { enabled: !!body });
  useEffect(() => onData?.(q.data), [q.data, onData]);

  if (!params.length) {
    return (
      <div className="sl-tab">
        <div className="oc-panel sl-empty">
          <Icon name="grid" size={20} />
          <div>
            <div className="sl-empty-title">
              {spec.name} has no numeric parameters to sweep.
            </div>
            <p className="subtle small">
              There is nothing to choose, so there is nothing to overfit. Pick a
              rule such as time-series momentum or the moving-average filter to
              see the overfitting diagnostics.
            </p>
          </div>
        </div>
      </div>
    );
  }
  const tooMany = draft.combos > maxCombos;
  const changed =
    JSON.stringify({ grid: draft.grid, n_partitions: Number(partitions) }) !==
    JSON.stringify(submitted);
  const run = () =>
    setSubmitted({ grid: draft.grid, n_partitions: Number(partitions) });

  return (
    <div className="sl-tab">
      <section className="oc-panel sl-controls">
        <div className="sl-controls-intro">
          <div className="eyebrow">Sweep</div>
          <p className="small">
            Re-run the rule for every combination of the values below, on the
            same data and costs as the backtest. The heatmap shows how sensitive
            the result is to the parameters; the diagnostics ask whether picking
            the best cell would have worked on data it had not seen.
          </p>
        </div>
        <GridEditor axes={axes} onChange={setAxes} params={params} />
        <div className="sl-controls-foot">
          <Field
            label="CSCV blocks (S)"
            info={{
              text: "The history is cut into S equal blocks; every way of choosing S/2 of them as in-sample is evaluated — C(16, 8) = 12,870 splits for S = 16. More blocks give more splits but shorter blocks.",
              reference: "Bailey et al. (2017), sec. 2",
            }}
          >
            <SegmentedControl
              size="sm"
              ariaLabel="CSCV partitions"
              options={["8", "10", "12", "16"]}
              value={partitions}
              onChange={setPartitions}
            />
          </Field>
          <div className="sl-run-status small">
            {draft.error ? (
              <span className="loss">{draft.error}</span>
            ) : tooMany ? (
              <span className="loss">
                {draft.combos} combinations — the server caps a sweep at{" "}
                {maxCombos}.
              </span>
            ) : (
              <span className="subtle">
                <span className="num">{draft.combos}</span> combinations
                {changed ? " · not run yet" : ""}
              </span>
            )}
          </div>
          <button
            type="button"
            className="btn btn-primary"
            disabled={
              !!draft.error || tooMany || !base || (!changed && !q.isError)
            }
            onClick={run}
          >
            <Icon name="grid" size={14} />{" "}
            {q.isFetching ? "Running sweep…" : "Run sweep"}
          </button>
        </div>
      </section>

      <Panel<SweepOut> query={q} skeletonHeight={260}>
        {(d) => <SweepVerdict d={d} />}
      </Panel>

      {!q.isError && q.data && (
        <>
          <div className="grid-2">
            <Panel<SweepOut>
              title="Sharpe across the grid"
              info={{
                text: "Annualized Sharpe ratio (after costs) of every parameter combination over the common live window. A smooth plateau is reassuring; an isolated bright cell surrounded by poor ones is the classic sign of a fitted fluke.",
              }}
              subtitle="Net-of-cost Sharpe for every combination. The outlined cell is the in-sample best; the dashed one is your current setting."
              query={q}
              notes={[]}
              skeletonHeight={340}
            >
              {(d) => <SharpeGrid d={d} base={base} />}
            </Panel>
            <Panel<SweepOut>
              title="Every combination’s equity curve"
              info={{
                text: "Growth of $1 for each parameter set in the sweep (weekly). The spread of the fan shows how much of the outcome is due to parameter choice rather than the idea itself.",
              }}
              subtitle="Grey lines are all combinations; the highlighted line is the in-sample best. A wide fan means the result depends heavily on the parameters."
              query={q}
              notes={[]}
              skeletonHeight={340}
            >
              {(d) => <EquityFan d={d} />}
            </Panel>
          </div>

          <div className="grid-2">
            <Panel<SweepOut>
              title="Probability of backtest overfitting"
              info={INFO.pbo}
              subtitle="Distribution of the logit λ of the in-sample winner’s out-of-sample rank across all CSCV splits. Red mass (λ ≤ 0) is the share of splits where the winner fell to the bottom half — that share is the PBO."
              query={q}
              notes={[]}
              skeletonHeight={300}
            >
              {(d) => <LogitView d={d} />}
            </Panel>
            <Panel<SweepOut>
              title="Performance degradation"
              info={INFO.degradation}
              subtitle="In-sample Sharpe of the chosen parameters (x) against their out-of-sample Sharpe (y), one dot per CSCV split. Points below the dashed 45° line lost performance out of sample."
              query={q}
              notes={[]}
              skeletonHeight={300}
            >
              {(d) => <DegradationScatter d={d} />}
            </Panel>
          </div>

          <div className="grid-2">
            <Panel<SweepOut>
              title="Deflated Sharpe ratio"
              info={INFO.dsr}
              subtitle="Is the best Sharpe better than the best of N strategies with no skill at all? The bar to beat rises with every combination you try."
              query={q}
              notes={[]}
              skeletonHeight={240}
            >
              {(d) => <DsrView d={d} />}
            </Panel>
            <Panel<SweepOut>
              title="Superior predictive ability"
              info={INFO.spa}
              subtitle={`Hansen’s test of whether any combination truly beats buy-and-hold of the benchmark, accounting for the whole search.`}
              query={q}
              notes={[]}
              skeletonHeight={240}
            >
              {(d) => <SpaView d={d} />}
            </Panel>
          </div>

          <Panel<SweepOut>
            title="All combinations"
            subtitle="Sortable. The highlighted row is the in-sample best."
            query={q}
            flush
            notes={[]}
            skeletonHeight={260}
          >
            {(d) => <ComboTable d={d} />}
          </Panel>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ verdict

function SweepVerdict({ d }: { d: SweepOut }) {
  const pbo = d.pbo.pbo;
  const dsr = d.deflated_sharpe;
  const spaP = d.spa.pvalue_consistent;
  const tone = !finite(pbo)
    ? "neutral"
    : pbo >= 0.5
      ? "loss"
      : pbo >= 0.25
        ? "warn"
        : "gain";
  const head = !finite(pbo)
    ? "Overfitting could not be measured on this sweep."
    : pbo >= 0.5
      ? "Picking the best parameters here is worse than a coin flip."
      : pbo >= 0.25
        ? "The in-sample winner often disappoints out of sample."
        : "The best parameters mostly hold up out of sample.";
  return (
    <Verdict
      eyebrow={`${d.combos.length} combinations · ${fmtDate(d.window.start)} – ${fmtDate(d.window.end)} · ${d.window.sessions.toLocaleString()} common sessions`}
      head={head}
      tone={tone}
      stats={[
        {
          label: "PBO",
          value: finite(pbo) ? fmtPct(pbo, 0) : "—",
          caption: d.pbo.n_combinations
            ? `${d.pbo.n_combinations.toLocaleString()} CSCV splits`
            : d.pbo.error,
          info: INFO.pbo,
          tone: tone === "gain" ? "gain" : tone === "neutral" ? "" : tone,
        },
        {
          label: "Deflated Sharpe",
          value: fmtProb(dsr.dsr),
          caption: `vs ${fmtProb(dsr.psr_vs_0)} undeflated`,
          info: INFO.dsr,
          tone: finite(dsr.dsr) ? (dsr.dsr >= 0.95 ? "gain" : "warn") : "",
        },
        {
          label: "SPA p-value",
          value: finite(spaP) ? spaP.toFixed(3) : "—",
          caption: `H₀: nothing beats ${d.benchmark.ticker}`,
          info: INFO.spa,
          tone: finite(spaP) ? (spaP < 0.05 ? "gain" : "warn") : "",
        },
        {
          label: "Best Sharpe",
          value: fmtSR(d.best.sharpe),
          caption: fmtParams(d.best.params),
          info: INFO.sharpe,
        },
        {
          label: "Luck alone",
          value: fmtSR(dsr.sr0_annualized),
          caption: `expected best of ${dsr.n_trials} null trials`,
          info: INFO.sr0,
        },
      ]}
    >
      <p>
        {finite(pbo) && d.pbo.n_combinations ? (
          <>
            In <span className="num">{fmtPct(pbo, 0)}</span> of the{" "}
            <span className="num">{d.pbo.n_combinations.toLocaleString()}</span>{" "}
            ways of splitting the history in half, the combination that looked
            best on one half ranked in the bottom half on the other.{" "}
          </>
        ) : (
          <>PBO could not be computed: {d.pbo.error}. </>
        )}
        The best combination earned a Sharpe of{" "}
        <span className="num">{fmtSR(d.best.sharpe)}</span>, but with{" "}
        <span className="num">{dsr.n_trials}</span> tries the best of worthless
        strategies would still show about{" "}
        <span className="num">{fmtSR(dsr.sr0_annualized)}</span>; the deflated
        Sharpe puts the probability that the winner is genuinely better than
        that at <span className="num">{fmtProb(dsr.dsr)}</span>.{" "}
        {finite(spaP) &&
          (spaP < 0.05 ? (
            <>
              Hansen’s SPA test (p ={" "}
              <span className="num">{spaP.toFixed(3)}</span>) finds that at
              least one combination beats buy-and-hold of {d.benchmark.ticker}{" "}
              by more than the search would produce by chance.
            </>
          ) : (
            <>
              Hansen’s SPA test (p ={" "}
              <span className="num">{spaP.toFixed(3)}</span>) cannot reject that
              no combination beats buy-and-hold of {d.benchmark.ticker} (Sharpe{" "}
              <span className="num">{fmtSR(d.benchmark.summary.sharpe)}</span>)
              once the whole search is accounted for.
            </>
          ))}
      </p>
    </Verdict>
  );
}

// ------------------------------------------------------------------ charts

function SharpeGrid({ d, base }: { d: SweepOut; base: BaseBody | null }) {
  const h = d.heatmap;
  const bestX = d.best.params[h.x_param];
  const cur = (k: string) =>
    (base?.params?.[k] as number | undefined) ??
    (d.base_params[k] as number | undefined);
  if (!h.y_param || !h.y_values) {
    const z = h.z as (number | null)[];
    return (
      <>
        <Bars
          series={[{ name: "Sharpe", x: h.x_values.map(label), y: z }]}
          colorBySign
          height={320}
          layout={{
            xaxis: {
              type: "category",
              title: { text: humanize(h.x_param) },
              showgrid: false,
            },
            yaxis: {
              title: { text: "Sharpe (net)" },
              tickformat: ".2f",
              zeroline: true,
            },
            margin: { l: 56, r: 8, t: 10, b: 44 },
          }}
        />
        <p className="subtle small">
          Best {humanize(h.x_param)} ={" "}
          <span className="num">{label(bestX)}</span>; your setting ={" "}
          <span className="num">{String(cur(h.x_param) ?? "—")}</span>.
        </p>
      </>
    );
  }
  const z = h.z as (number | null)[][];
  const bx = h.x_values.indexOf(bestX);
  const by = h.y_values.indexOf(d.best.params[h.y_param]);
  const cx = h.x_values.indexOf(Number(cur(h.x_param)));
  const cy = h.y_values.indexOf(Number(cur(h.y_param)));
  const rect = (x: number, y: number, dash: string) => ({
    type: "rect",
    xref: "x",
    yref: "y",
    x0: x - 0.5,
    x1: x + 0.5,
    y0: y - 0.5,
    y1: y + 0.5,
    line: { color: "var(--text)", width: 2, dash },
  });
  const shapes = [
    ...(bx >= 0 && by >= 0 ? [rect(bx, by, "solid")] : []),
    ...(cx >= 0 && cy >= 0 && (cx !== bx || cy !== by)
      ? [rect(cx, cy, "dot")]
      : []),
  ];
  return (
    <HeatmapChart
      x={h.x_values.map(label)}
      y={h.y_values.map(label)}
      z={z}
      format="num"
      digits={2}
      diverging
      showValues={h.x_values.length * h.y_values.length <= 64}
      height={Math.max(300, h.y_values.length * 44 + 80)}
      layout={
        {
          xaxis: { title: { text: humanize(h.x_param) } },
          yaxis: { title: { text: humanize(h.y_param) }, autorange: true },
          margin: { l: 8, r: 8, t: 8, b: 44 },
          shapes,
        } as any
      }
    />
  );
}

function EquityFan({ d }: { d: SweepOut }) {
  const f = d.equity_weekly;
  const best = `#${d.best.combo}`;
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => {
        const x = f.index as string[];
        const others = f.columns
          .filter((c) => c !== best)
          .map(
            (c) =>
              ({
                type: "scatter",
                mode: "lines",
                x,
                y: f.data[c],
                name: fmtParams(d.combos[Number(c.slice(1))]),
                line: { color: t.text3, width: 0.8 },
                opacity: 0.35,
                hoverinfo: "skip",
                showlegend: false,
              }) as Data,
          );
        return [
          ...others,
          {
            type: "scatter",
            mode: "lines",
            x,
            y: f.data[best],
            name: `Best: ${fmtParams(d.best.params)}`,
            line: { color: t.categorical[0], width: 2.2 },
            hovertemplate: "<b>Best</b> $%{y:,.2f}<extra></extra>",
          } as Data,
        ];
      },
    [f, best, d.best.params, d.combos],
  );
  return (
    <Chart
      data={data}
      layout={
        {
          hovermode: "x unified",
          showlegend: true,
          xaxis: { type: "date" },
          yaxis: { type: "log", tickformat: "$,.2f", side: "right" },
          margin: { l: 8, r: 8, t: 30, b: 28 },
        } as any
      }
      height={320}
    />
  );
}

function LogitView({ d }: { d: SweepOut }) {
  const p = d.pbo;
  if (p.error || !p.logits)
    return (
      <p className="subtle small">
        PBO not available: {p.error ?? "no logits returned"}.
      </p>
    );
  return (
    <>
      <BinnedHistogram
        counts={p.logits.counts}
        edges={p.logits.edges}
        split={0}
        xTitle="λ = logit of the winner’s out-of-sample relative rank"
        vlines={[
          { x: 0, label: `PBO ${fmtPct(p.pbo, 0)}`, color: "var(--text)" },
        ]}
        height={260}
      />
      <div className="sl-mini-stats small">
        <span>
          <span className="subtle">Median λ</span>{" "}
          <span className="num">{fmtSR(p.logits.median)}</span>
        </span>
        <span>
          <span className="subtle">Mean λ</span>{" "}
          <span className="num">{fmtSR(p.logits.mean)}</span>
        </span>
        <span>
          <span className="subtle">Blocks × splits</span>{" "}
          <span className="num">
            {p.n_partitions} × {p.n_combinations?.toLocaleString()}
          </span>
        </span>
        <span>
          <span className="subtle">Sessions used</span>{" "}
          <span className="num">{p.rows_used?.toLocaleString()}</span>
        </span>
      </div>
    </>
  );
}

function DegradationScatter({ d }: { d: SweepOut }) {
  const g = d.pbo.degradation;
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => {
        if (!g) return [];
        const xs = g.is_sharpe_ann;
        const lo = Math.min(...xs, ...g.oos_sharpe_ann);
        const hi = Math.max(...xs, ...g.oos_sharpe_ann);
        const k = Math.sqrt(252);
        const out: Data[] = [
          {
            type: "scatter",
            mode: "lines",
            x: [lo, hi],
            y: [lo, hi],
            name: "45° (no decay)",
            line: { color: t.ruleStrong, width: 1, dash: "dash" },
            hoverinfo: "skip",
          } as Data,
          {
            type: "scatter",
            mode: "markers",
            x: xs,
            y: g.oos_sharpe_ann,
            name: "CSCV split",
            marker: {
              size: 5,
              color: g.oos_sharpe_ann.map((v) =>
                v < 0 ? t.loss : t.categorical[0],
              ),
              opacity: 0.55,
            },
            hovertemplate: "IS %{x:.2f} → OOS <b>%{y:.2f}</b><extra></extra>",
          } as Data,
        ];
        if (finite(g.slope) && finite(g.intercept)) {
          const xmin = Math.min(...xs);
          const xmax = Math.max(...xs);
          // regression is on per-period Sharpe: y = a + b x  →  annualized: Y = a√252 + b X
          out.push({
            type: "scatter",
            mode: "lines",
            x: [xmin, xmax],
            y: [
              g.intercept * k + g.slope * xmin,
              g.intercept * k + g.slope * xmax,
            ],
            name: `Fit: slope ${g.slope.toFixed(2)}`,
            line: { color: t.text, width: 1.8 },
            hoverinfo: "skip",
          } as Data);
        }
        return out;
      },
    [g],
  );
  if (!g)
    return (
      <p className="subtle small">
        Degradation regression not available
        {d.pbo.error ? `: ${d.pbo.error}` : ""}.
      </p>
    );
  return (
    <>
      <Chart
        data={data}
        layout={
          {
            showlegend: true,
            legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom" },
            hovermode: "closest",
            xaxis: {
              title: { text: "In-sample Sharpe (annualized)" },
              zeroline: true,
              showspikes: false,
            },
            yaxis: { title: { text: "Out-of-sample Sharpe" }, zeroline: true },
            margin: { l: 52, r: 12, t: 34, b: 44 },
          } as any
        }
        height={290}
      />
      <div className="sl-mini-stats small">
        <span>
          <span className="subtle">Slope</span>{" "}
          <span
            className={`num ${finite(g.slope) && g.slope < 0 ? "loss" : ""}`}
          >
            {fmtSR(g.slope)}
          </span>
        </span>
        <span>
          <span className="subtle">R²</span>{" "}
          <span className="num">{fmtSR(g.r2)}</span>
        </span>
        <span>
          <span className="subtle">P(OOS Sharpe &lt; 0)</span>{" "}
          <span className="num">{fmtProb(g.prob_oos_loss)}</span>
        </span>
      </div>
    </>
  );
}

function DsrView({ d }: { d: SweepOut }) {
  const s = d.deflated_sharpe;
  if (s.error) return <p className="subtle small">{s.error}</p>;
  const pts = [
    { v: 0, label: "Zero", cls: "sl-nl-zero" },
    {
      v: s.sr0_annualized ?? 0,
      label: `Luck: best of ${s.n_trials}`,
      cls: "sl-nl-luck",
    },
    { v: s.sharpe_ann ?? 0, label: "Selected", cls: "sl-nl-best" },
  ];
  const lo = Math.min(0, ...pts.map((p) => p.v)) - 0.1;
  const hi = Math.max(...pts.map((p) => p.v)) + 0.15;
  const pos = (v: number) => `${((v - lo) / (hi - lo)) * 100}%`;
  return (
    <div className="sl-dsr">
      <div className="sl-numberline" aria-label="Sharpe number line">
        <div className="sl-nl-track" />
        <div
          className="sl-nl-band"
          style={{
            left: pos(0),
            width: `calc(${pos(s.sr0_annualized ?? 0)} - ${pos(0)})`,
          }}
        />
        {pts.map((p) => (
          <div
            key={p.cls}
            className={`sl-nl-pt ${p.cls}`}
            style={{ left: pos(p.v) }}
          >
            <span className="sl-nl-dot" />
            <span className="sl-nl-val num">{fmtSR(p.v)}</span>
            <span className="sl-nl-lab">{p.label}</span>
          </div>
        ))}
      </div>
      <div className="sl-dsr-row">
        <div>
          <div className="sl-dsr-big num">{fmtProb(s.psr_vs_0)}</div>
          <div className="subtle small">PSR — beats zero</div>
        </div>
        <Icon name="arrow-right" size={18} />
        <div>
          <div
            className={`sl-dsr-big num ${finite(s.dsr) ? (s.dsr >= 0.95 ? "gain" : "warn") : ""}`}
          >
            {fmtProb(s.dsr)}
          </div>
          <div className="subtle small">
            DSR — beats the luckiest of {s.n_trials}
          </div>
        </div>
      </div>
      <p className="small">
        The shaded band is what {s.n_trials} strategies with no edge would reach
        by chance, given how much their Sharpe ratios differ from each other.{" "}
        {finite(s.dsr) && s.dsr >= 0.95
          ? "The selected combination clears it with 95% confidence."
          : "The selected combination does not clear it with 95% confidence — its advantage over the rest of the grid is within what selection alone produces."}
      </p>
    </div>
  );
}

function SpaView({ d }: { d: SweepOut }) {
  const s = d.spa;
  if (s.error) return <p className="subtle small">{s.error}</p>;
  const rows = [
    {
      k: "Consistent",
      v: s.pvalue_consistent,
      note: "Hansen’s recommended p-value",
    },
    {
      k: "Lower bound",
      v: s.pvalue_lower,
      note: "most liberal (White-like lower bound)",
    },
    {
      k: "Upper bound",
      v: s.pvalue_upper,
      note: "most conservative (White’s Reality Check)",
    },
  ];
  const p = s.pvalue_consistent;
  return (
    <div className="sl-spa">
      <div className="sl-spa-bars">
        {rows.map((r) => (
          <div key={r.k} className="sl-spa-row">
            <span className="sl-spa-k small">{r.k}</span>
            <div className="sl-spa-track">
              <div
                className={`sl-spa-fill ${finite(r.v) && r.v < 0.05 ? "gain" : ""}`}
                style={{ width: `${Math.max(1, (r.v ?? 0) * 100)}%` }}
              />
              <div
                className="sl-spa-mark"
                style={{ left: "5%" }}
                title="5% significance"
              />
            </div>
            <span className="num sl-spa-v">
              {finite(r.v) ? r.v.toFixed(3) : "—"}
            </span>
          </div>
        ))}
        <div className="small subtle">
          Bars run from 0 to 1; the black tick marks p = 0.05. A bar that stops
          left of the tick is significant.
        </div>
      </div>
      <p className="small">
        {finite(p) && p < 0.05
          ? `With p = ${p.toFixed(3)}, the best of the ${s.n_models} combinations beats buy-and-hold of ${d.benchmark.ticker} by more than a search of this size would find by chance.`
          : `With p = ${finite(p) ? p.toFixed(3) : "—"}, the evidence that any of the ${s.n_models} combinations beats buy-and-hold of ${d.benchmark.ticker} is no stronger than what searching ${s.n_models} worthless variants would produce.`}
      </p>
      <p className="subtle small sl-method">
        Stationary bootstrap, block ≈ {s.block_size} sessions,{" "}
        {s.reps?.toLocaleString()} replications,{" "}
        {s.observations?.toLocaleString()} sessions. Benchmark:{" "}
        {d.benchmark.ticker} buy-and-hold, no costs.
      </p>
    </div>
  );
}

function ComboTable({ d }: { d: SweepOut }) {
  const keys = Object.keys(d.grid);
  type Row = SweepOut["table"][number];
  const cols: Column<Row>[] = [
    ...keys.map((k) => ({
      key: k,
      label: humanize(k),
      numeric: true,
      format: (v: number) => label(v),
    })),
    {
      key: "sharpe",
      label: "Sharpe",
      numeric: true,
      format: (v: number) => fmtSR(v),
      info: INFO.sharpe,
    },
    {
      key: "sortino",
      label: "Sortino",
      numeric: true,
      format: (v: number) => fmtSR(v),
      hideBelow: 900,
    },
    {
      key: "cagr",
      label: "CAGR",
      numeric: true,
      format: (v: number) => fmtPct(v, 1),
      color: "sign",
    },
    {
      key: "ann_vol",
      label: "Vol",
      numeric: true,
      format: (v: number) => fmtPct(v, 1),
      hideBelow: 600,
    },
    {
      key: "max_drawdown",
      label: "Max DD",
      numeric: true,
      format: (v: number) => fmtPct(v, 1),
      hideBelow: 600,
    },
    {
      key: "annual_turnover",
      label: "Turnover",
      numeric: true,
      format: (v: number) => fmtMultiple(v, 1),
      hideBelow: 900,
    },
    {
      key: "picked",
      label: "Picked in CSCV",
      numeric: true,
      value: (r) => d.pbo.selected_counts?.[r.combo] ?? null,
      format: (v: number | null) =>
        v == null || !d.pbo.n_combinations
          ? "—"
          : fmtPct(v / d.pbo.n_combinations, 1),
      hideBelow: 1200,
      info: {
        text: "Share of CSCV splits in which this combination was the in-sample winner. Winners that change from split to split are a symptom of fitting noise.",
      },
    },
  ];
  return (
    <DataTable
      columns={cols}
      rows={d.table}
      rowKey={(r) => r.combo}
      defaultSort={{ key: "sharpe", dir: "desc" }}
      isActive={(r) => r.combo === d.best.combo}
      maxHeight={420}
    />
  );
}
