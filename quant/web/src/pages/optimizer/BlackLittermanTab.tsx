/**
 * Black–Litterman tab: a views editor (absolute / relative views with confidence) and what
 * the model did with them — prior vs posterior expected returns, each view's pull, and the
 * prior vs resulting weights. Everything shown comes from POST /portfolio/optimize.
 */
import { useMemo } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import type { Data } from "plotly.js";
import {
  Callout,
  Chart,
  DataTable,
  EmptyState,
  InfoTip,
  NumberField,
  Panel,
  SegmentedControl,
  Select,
  Slider,
  StatGrid,
  StatTile,
  type Column,
} from "../../components";
import { Icon } from "../../components/Icon";
import type { ApiError } from "../../lib/api";
import { fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import type { Tokens } from "../../lib/theme";
import { newViewId, validViews, type ViewDraft } from "./config";
import { INFO } from "./info";
import { QPanel, sortBy, useOpt } from "./shared";
import type { BLView, OptimizeOut } from "./types";

export function BlackLittermanTab({
  q,
}: {
  q: UseQueryResult<OptimizeOut, ApiError>;
}) {
  const { cfg, set, spec } = useOpt();
  const active = cfg.returns === "black_litterman";
  const e = q.data?.expected_returns;
  const live = active && e?.model === "black_litterman";

  return (
    <div className="stack-lg">
      {!active && (
        <Callout
          tone="info"
          title="Black–Litterman is not the active expected-return model"
        >
          <div className="op-callout-row">
            <span>
              Your views only matter when the optimiser uses Black–Litterman
              returns. Switching also affects the Allocation and Frontier tabs.
              It needs a risk-free rate and the benchmark ({cfg.benchmark}) in
              the data.
            </span>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => set({ returns: "black_litterman" })}
            >
              Use Black–Litterman
            </button>
          </div>
        </Callout>
      )}

      {active && spec && !spec.needs_expected_returns && (
        <Callout tone="warn" title={`${spec.label} ignores expected returns`}>
          Your views change the posterior returns below, but not the weights:
          this method allocates from risk alone. Pick Maximum Sharpe,
          Mean–variance or Minimum CVaR (with a return floor) to let the views
          move the portfolio.
        </Callout>
      )}

      <div className="op-bl-grid">
        <Panel
          title="Your views"
          info={INFO.blPosterior}
          subtitle="State what you believe, and how strongly. Absolute: “GLD will return 6 % a year”. Relative: “XLE will beat XLK by 3 % a year”. Views you do not hold with any confidence are ignored."
        >
          <ViewsEditor mu={q.data?.expected_returns.mu} />
        </Panel>

        {active ? (
          <QPanel<OptimizeOut>
            q={q}
            title="Prior vs posterior"
            info={INFO.blPosterior}
            subtitle="Expected annual return per asset: the equilibrium prior (what the prior portfolio implies) and the posterior after your views."
            skeletonHeight={320}
          >
            {(d) =>
              d.expected_returns.model === "black_litterman" ? (
                <PriorPosterior d={d} />
              ) : null
            }
          </QPanel>
        ) : (
          <Panel
            title="Prior vs posterior"
            info={INFO.blPosterior}
            subtitle="Appears once Black–Litterman is the active model."
          >
            <EmptyState icon="scale" title="Not using Black–Litterman yet">
              Switch the expected-return model to see the prior and posterior.
            </EmptyState>
          </Panel>
        )}
      </div>

      {live && q.data && (
        <>
          <Panel
            title="The model's reading of your views"
            info={INFO.priorImplied}
            subtitle="For each view: what the prior already implied, what you said, and where the posterior settled. The posterior moves toward your view in proportion to your confidence and to how far the view is from the prior."
            flush
            notes="Posterior-implied values are computed in your browser from the posterior returns (long − short for relative views)."
          >
            <ViewsTable d={q.data} />
          </Panel>
          <div className="grid-2">
            <Panel
              title="Model settings"
              info={INFO.blDelta}
              subtitle={
                q.data.expected_returns.prior_source
                  ? `Prior weights: ${q.data.expected_returns.prior_source}`
                  : undefined
              }
            >
              <StatGrid min={130}>
                <StatTile
                  label="Risk aversion δ"
                  value={q.data.expected_returns.params.delta ?? null}
                  format={(v) => fmtNum(v, 2)}
                  info={INFO.blDelta}
                  caption={String(
                    q.data.expected_returns.params.delta_source ?? "",
                  )}
                />
                <StatTile
                  label="τ"
                  value={q.data.expected_returns.params.tau ?? null}
                  format={(v) => fmtNum(v, 3)}
                  info={INFO.blTau}
                />
                <StatTile
                  label="Risk-free"
                  value={q.data.expected_returns.params.risk_free ?? null}
                  format={(v) => fmtPct(v, 2)}
                  info={INFO.riskFree}
                  caption={
                    q.data.risk_free.source === "user"
                      ? "user-supplied"
                      : "3M T-bill"
                  }
                />
                <StatTile
                  label="Views used"
                  value={validViews(cfg).filter((v) => v.confidence > 0).length}
                  format={(v) => fmtNum(v, 0)}
                />
              </StatGrid>
            </Panel>
            <Panel
              title="Prior weights vs Black–Litterman weights"
              info={{
                text: "The prior portfolio the equilibrium is built from, next to the unconstrained BL portfolio (δΣ)⁻¹μ and the weights the chosen method actually picked under your constraints.",
                formula: "w_{BL} = (\\delta \\Sigma_{post})^{-1} \\mu^{e}_{BL}",
              }}
              notes={[]}
            >
              <WeightsCompare d={q.data} />
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}

/** New views start AT the current model's expectation (no change) — you then move them. */
function ViewsEditor({ mu }: { mu?: Record<string, number> }) {
  const { cfg, set } = useOpt();
  const tks = cfg.tickers;
  const upd = (id: string, p: Partial<ViewDraft>) =>
    set({ views: cfg.views.map((v) => (v.id === id ? { ...v, ...p } : v)) });
  const add = (kind: ViewDraft["kind"]) => {
    const long = tks[0] ?? "";
    const short = tks.find((t) => t !== long) ?? "";
    const m = (k: string) => mu?.[k] ?? 0;
    const value = +(kind === "absolute" ? m(long) : m(long) - m(short)).toFixed(
      4,
    );
    set({
      views: [
        ...cfg.views,
        { id: newViewId(), kind, long, short, value, confidence: 0.5 },
      ],
    });
  };
  const invalid = new Set(
    cfg.views
      .filter((v) => !validViews({ views: [v], tickers: tks }).length)
      .map((v) => v.id),
  );
  return (
    <div className="op-views">
      {cfg.views.length === 0 && (
        <div className="op-views-empty subtle small">
          No views yet — with none, the posterior equals the prior. A new view
          starts at the model's current expectation for that asset (so it
          changes nothing) — then move it to what you believe.
        </div>
      )}
      {cfg.views.map((v, i) => (
        <div
          key={v.id}
          className={`op-view ${invalid.has(v.id) ? "invalid" : ""}`}
        >
          <div className="op-view-top">
            <span className="op-view-n num">
              {String(i + 1).padStart(2, "0")}
            </span>
            <SegmentedControl
              size="sm"
              ariaLabel="View type"
              options={[
                { value: "absolute", label: "Absolute" },
                { value: "relative", label: "Relative" },
              ]}
              value={v.kind}
              onChange={(kind) => upd(v.id, { kind })}
            />
            <span className="spacer" />
            <button
              type="button"
              className="icon-btn"
              aria-label="Remove view"
              onClick={() =>
                set({ views: cfg.views.filter((x) => x.id !== v.id) })
              }
            >
              <Icon name="trash" size={14} />
            </button>
          </div>
          <div className="op-view-sentence">
            <Select<string>
              ariaLabel="Asset"
              value={v.long}
              onChange={(long) => upd(v.id, { long })}
              options={tks}
            />
            {v.kind === "relative" ? (
              <>
                <span>beats</span>
                <Select<string>
                  ariaLabel="Versus"
                  value={v.short}
                  onChange={(short) => upd(v.id, { short })}
                  options={tks.filter((t) => t !== v.long)}
                />
                <span>by</span>
              </>
            ) : (
              <span>returns</span>
            )}
            <NumberField
              value={v.value}
              percent
              min={-1}
              max={1}
              step={0.005}
              width={92}
              onChange={(value) => upd(v.id, { value })}
            />
            <span className="subtle">a year</span>
          </div>
          <Slider
            label="Confidence"
            value={v.confidence}
            min={0}
            max={1}
            step={0.05}
            format={(x) => fmtPct(x, 0)}
            onChange={(confidence) => upd(v.id, { confidence })}
            info={INFO.blConfidence}
          />
          {invalid.has(v.id) && (
            <div className="op-warn small">
              This view refers to an asset outside the universe (or compares an
              asset with itself) and is not sent.
            </div>
          )}
        </div>
      ))}
      <div className="row-wrap">
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => add("absolute")}
          disabled={tks.length < 1}
        >
          <Icon name="plus" size={14} /> Absolute view
        </button>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => add("relative")}
          disabled={tks.length < 2}
        >
          <Icon name="plus" size={14} /> Relative view
        </button>
        <span className="spacer" />
        <span className="op-tau">
          <NumberField
            label={<>τ</>}
            value={cfg.tau}
            min={0.001}
            max={1}
            step={0.005}
            width={84}
            onChange={(tau) => set({ tau })}
            info={INFO.blTau}
          />
        </span>
      </div>
    </div>
  );
}

function PriorPosterior({ d }: { d: OptimizeOut }) {
  const e = d.expected_returns;
  const rf = Number(e.params.risk_free ?? 0);
  const order = useMemo(() => sortBy(d.universe.tickers, e.mu), [d, e]);
  const touched = useMemo(
    () => new Set((e.views ?? []).flatMap((v) => [v.long, v.short ?? ""])),
    [e],
  );
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => [
        {
          type: "bar",
          name: "Prior (equilibrium)",
          x: order,
          y: order.map((k) => (e.prior_excess?.[k] ?? NaN) + rf),
          marker: { color: t.text3, opacity: 0.5 },
          hovertemplate: "<b>%{x}</b> prior %{y:.2%}<extra></extra>",
        } as Data,
        {
          type: "bar",
          name: "Posterior (with your views)",
          x: order,
          y: order.map((k) => e.mu[k]),
          marker: {
            color: t.categorical[0],
            line: {
              color: order.map((k) =>
                touched.has(k) ? t.text : t.categorical[0],
              ),
              width: order.map((k) => (touched.has(k) ? 2 : 0)),
            },
          },
          hovertemplate: "<b>%{x}</b> posterior %{y:.2%}<extra></extra>",
        } as Data,
      ],
    [e, order, rf, touched],
  );
  const layout = useMemo(
    () => ({
      barmode: "group",
      bargap: 0.3,
      margin: { l: 8, r: 8, t: 30, b: 28 },
      legend: { orientation: "h", y: 1.02, yanchor: "bottom" },
      yaxis: { tickformat: ".0%", zeroline: true, side: "right" },
      xaxis: { type: "category", showspikes: false },
    }),
    [],
  );
  return (
    <>
      <Chart
        data={data}
        layout={layout as never}
        height={300}
        ariaLabel="Prior versus posterior expected returns"
      />
      <div className="subtle small op-legend-note">
        <span className="op-swatch op-swatch-outline" /> outlined: assets named
        in a view · total return = excess return + risk-free rate ({fmtPct(rf, 2)})
      </div>
    </>
  );
}

function ViewsTable({ d }: { d: OptimizeOut }) {
  const e = d.expected_returns;
  const rf = Number(e.params.risk_free ?? 0);
  type Row = BLView & { posterior: number | null; pull: number | null };
  const rows = useMemo<Row[]>(
    () =>
      (e.views ?? []).map((v) => {
        const post = e.posterior_excess;
        const posterior = post
          ? v.short
            ? (post[v.long] ?? NaN) - (post[v.short] ?? NaN)
            : (post[v.long] ?? NaN) + rf
          : null;
        const gap = v.value - v.prior_implied;
        return {
          ...v,
          posterior,
          pull:
            posterior != null && Math.abs(gap) > 1e-9
              ? (posterior - v.prior_implied) / gap
              : null,
        };
      }),
    [e, rf],
  );
  if (!rows.length)
    return (
      <div className="op-pad subtle small">
        No views with positive confidence were sent — the posterior is the
        prior.
      </div>
    );
  const cols: Column<Row>[] = [
    { key: "label", label: "View", render: (r) => <span>{r.label}</span> },
    {
      key: "confidence",
      label: "Confidence",
      numeric: true,
      format: (v) => fmtPct(v, 0),
      info: INFO.blConfidence,
    },
    {
      key: "prior_implied",
      label: "Prior implied",
      numeric: true,
      format: (v) => fmtSignedPct(v, 2),
      info: INFO.priorImplied,
    },
    {
      key: "value",
      label: "Your view",
      numeric: true,
      format: (v) => fmtSignedPct(v, 2),
    },
    {
      key: "posterior",
      label: "Posterior",
      numeric: true,
      format: (v) => fmtSignedPct(v, 2),
    },
    {
      key: "pull",
      label: "Moved toward view",
      numeric: true,
      format: (v) => fmtPct(v, 0),
      heat: { min: 0, max: 1, diverging: false },
      info: {
        text: "How much of the distance between the prior and your view the posterior travelled: 0 % = ignored, 100 % = fully adopted. Driven by your confidence and by the view's correlation with other assets.",
      },
    },
    {
      key: "omega",
      label: "Ω",
      numeric: true,
      format: (v) => fmtNum(v, 5),
      hideBelow: 900,
      info: {
        title: "View uncertainty Ω",
        text: "The variance of the view's error term that the confidence maps to (Idzorek). Smaller Ω = stronger view.",
        reference: "Idzorek (2005)",
      },
    },
  ];
  return <DataTable<Row> columns={cols} rows={rows} rowKey={(r) => r.label} />;
}

function WeightsCompare({ d }: { d: OptimizeOut }) {
  const e = d.expected_returns;
  const order = useMemo(
    () => sortBy(d.universe.tickers, d.result.weights),
    [d],
  );
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => [
        {
          type: "bar",
          name: "Prior",
          x: order,
          y: order.map((k) => e.market_weights?.[k] ?? null),
          marker: { color: t.text3, opacity: 0.5 },
          hovertemplate: "<b>%{x}</b> prior %{y:.1%}<extra></extra>",
        } as Data,
        {
          type: "bar",
          name: "Unconstrained BL",
          x: order,
          y: order.map((k) => e.unconstrained_bl_weights?.[k] ?? null),
          marker: { color: t.categorical[1] },
          hovertemplate: "<b>%{x}</b> BL %{y:.1%}<extra></extra>",
        } as Data,
        {
          type: "bar",
          name: "Chosen method",
          x: order,
          y: order.map((k) => d.result.weights[k]),
          marker: { color: t.categorical[0] },
          hovertemplate: "<b>%{x}</b> chosen %{y:.1%}<extra></extra>",
        } as Data,
      ],
    [d, e, order],
  );
  const layout = useMemo(
    () => ({
      barmode: "group",
      bargap: 0.25,
      margin: { l: 8, r: 8, t: 30, b: 28 },
      legend: { orientation: "h", y: 1.02, yanchor: "bottom" },
      yaxis: { tickformat: ".0%", zeroline: true, side: "right" },
      xaxis: { type: "category", showspikes: false },
    }),
    [],
  );
  return (
    <>
      <Chart
        data={data}
        layout={layout as never}
        height={280}
        ariaLabel="Prior and Black-Litterman weights"
      />
      <div className="subtle small op-legend-note">
        Chosen method: {d.method.label}{" "}
        <InfoTip
          size={12}
          info={{
            text: "Black–Litterman supplies expected returns (and the posterior covariance); the allocation method you picked turns them into weights under your constraints.",
          }}
        />
      </div>
    </>
  );
}
