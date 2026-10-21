/**
 * VIEWS · BL: the views editor (absolute and relative, each with a confidence) and what
 * Black–Litterman did with them: prior against posterior returns and weights per asset, each
 * view read back (prior-implied, stated, posterior, share of the gap travelled), and the
 * model's parameters. Everything shown comes from POST /portfolio/optimize.
 */
import { useMemo } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { DataTable, NumberField, Panel, SegmentedControl, Select, Slider, StatGrid, StatTile, type Column } from "../../components";
import { Absent, Note } from "../../design";
import type { ApiError } from "../../lib/api";
import { fmtNum, fmtPct } from "../../lib/format";
import { newViewId, validViews, type ViewDraft } from "./config";
import { viewRows, type ViewRow } from "./derive";
import { INFO, METHOD_CODE } from "./info";
import { QPanel, Readline, sortBy, sym, useOpt } from "./shared";
import type { OptimizeOut } from "./types";

export function BlackLittermanTab({ q }: { q: UseQueryResult<OptimizeOut, ApiError> }) {
  const { cfg, set, spec } = useOpt();
  const active = cfg.returns === "black_litterman";
  const live = active && q.data?.expected_returns.model === "black_litterman";
  const asOf = q.data?.universe.end;

  return (
    <div className="op-tab-body">
      {!active && (
        <div className="op-strip">
          <span className="num">μ MODEL · {cfg.returns.replace(/_/g, " ").toUpperCase()} · VIEWS INACTIVE</span>
          <button type="button" className="btn btn-sm btn-primary" onClick={() => set({ returns: "black_litterman" })}>
            USE BLACK–LITTERMAN
          </button>
        </div>
      )}
      {active && spec && !spec.needs_expected_returns && (
        <div className="op-strip">
          <span className="num">{METHOD_CODE[spec.name]} IGNORES μ · VIEWS MOVE THE POSTERIOR, NOT THE WEIGHTS</span>
        </div>
      )}

      <div className="op-bl-grid">
        <Panel
          title={
            <>
              Views · {validViews(cfg).filter((v) => v.confidence > 0).length}
              <Note n={4} to="black-litterman" />
            </>
          }
        >
          <ViewsEditor mu={q.data?.expected_returns.mu} />
        </Panel>

        {active ? (
          <QPanel<OptimizeOut> q={q} title="Prior · posterior" asOf={asOf} flush skeletonHeight={300} notes={[]}>
            {(d) => (d.expected_returns.model === "black_litterman" ? <PriorPosterior d={d} /> : <Absent reason="POSTERIOR NOT RETURNED" source="POST /api/portfolio/optimize" />)}
          </QPanel>
        ) : (
          <Panel title="Prior · posterior">
            <Absent reason="BLACK–LITTERMAN NOT ACTIVE" source="RETURNS MODEL" />
          </Panel>
        )}
      </div>

      {live && q.data && (
        <>
          <Panel title="Views · read back" asOf={asOf} flush notes={["Posterior-implied values are computed in the browser from the posterior returns (long − short for relative views)."]}>
            <ViewsTable d={q.data} />
          </Panel>
          <Panel title={`BL · parameters${q.data.expected_returns.prior_source ? ` · prior ${q.data.expected_returns.prior_source}` : ""}`} asOf={asOf}>
            <StatGrid min={130}>
              <StatTile label={sym("δ")} value={q.data.expected_returns.params.delta ?? null} format={(v) => fmtNum(v, 2)} info={INFO.blDelta} caption={String(q.data.expected_returns.params.delta_source ?? "").toUpperCase()} />
              <StatTile label={sym("τ")} value={q.data.expected_returns.params.tau ?? null} format={(v) => fmtNum(v, 3)} info={INFO.blTau} />
              <StatTile label="RF" value={q.data.expected_returns.params.risk_free ?? null} format={(v) => fmtPct(v, 2)} info={INFO.riskFree} caption={q.data.risk_free.source === "user" ? "MANUAL" : "3M BILL"} />
              <StatTile label="Views" value={validViews(cfg).filter((v) => v.confidence > 0).length} format={(v) => fmtNum(v, 0)} caption="CONF > 0" />
            </StatGrid>
          </Panel>
        </>
      )}
    </div>
  );
}

const round4 = (x: number) => Math.round(x * 1e4) / 1e4;

/** New views start AT the current model's expectation (no change); you then move them. */
function ViewsEditor({ mu }: { mu?: Record<string, number> }) {
  const { cfg, set } = useOpt();
  const tks = cfg.tickers;
  const upd = (id: string, p: Partial<ViewDraft>) => set({ views: cfg.views.map((v) => (v.id === id ? { ...v, ...p } : v)) });
  const add = (kind: ViewDraft["kind"]) => {
    const long = tks[0] ?? "";
    const short = tks.find((t) => t !== long) ?? "";
    const m = (k: string) => mu?.[k] ?? 0;
    const value = round4(kind === "absolute" ? m(long) : m(long) - m(short));
    set({ views: [...cfg.views, { id: newViewId(), kind, long, short, value, confidence: 0.5 }] });
  };
  const invalid = new Set(cfg.views.filter((v) => !validViews({ views: [v], tickers: tks }).length).map((v) => v.id));
  return (
    <div className="op-views">
      {cfg.views.length === 0 && <div className="op-views-empty num">NO VIEWS · POSTERIOR = PRIOR</div>}
      {cfg.views.map((v, i) => (
        <div key={v.id} className={`op-view ${invalid.has(v.id) ? "invalid" : ""}`}>
          <div className="op-view-top">
            <span className="op-view-n num">{String(i + 1).padStart(2, "0")}</span>
            <SegmentedControl
              size="sm"
              ariaLabel="View type"
              options={[
                { value: "absolute", label: "ABS" },
                { value: "relative", label: "REL" },
              ]}
              value={v.kind}
              onChange={(kind) => upd(v.id, { kind })}
            />
            <span className="spacer" />
            <button type="button" className="op-x" aria-label={`Remove view ${i + 1}`} onClick={() => set({ views: cfg.views.filter((x) => x.id !== v.id) })}>
              ×
            </button>
          </div>
          <div className="op-view-sentence num">
            <Select<string> ariaLabel="Asset" value={v.long} onChange={(long) => upd(v.id, { long })} options={tks} />
            {v.kind === "relative" ? (
              <>
                <span>−</span>
                <Select<string> ariaLabel="Versus" value={v.short} onChange={(short) => upd(v.id, { short })} options={tks.filter((t) => t !== v.long)} />
                <span>=</span>
              </>
            ) : (
              <span>=</span>
            )}
            <NumberField ariaLabel="View, annual return" value={v.value} percent min={-1} max={1} step={0.005} width={92} onChange={(value) => upd(v.id, { value })} />
            <span className="op-rf-unit">ANN</span>
          </div>
          <Slider label="CONF" value={v.confidence} min={0} max={1} step={0.05} format={(x) => fmtPct(x, 0)} onChange={(confidence) => upd(v.id, { confidence })} />
          {invalid.has(v.id) && <div className="op-flag loss num">NOT SENT · ASSET OUTSIDE UNIVERSE OR SELF</div>}
        </div>
      ))}
      <div className="op-rail-row">
        <button type="button" className="btn btn-sm" onClick={() => add("absolute")} disabled={tks.length < 1}>
          + ABS
        </button>
        <button type="button" className="btn btn-sm" onClick={() => add("relative")} disabled={tks.length < 2}>
          + REL
        </button>
        <span className="spacer" />
        <NumberField label={sym("τ")} ariaLabel="Tau" value={cfg.tau} min={0.001} max={1} step={0.005} width={84} onChange={(tau) => set({ tau })} />
      </div>
    </div>
  );
}

type PPRow = { ticker: string; view: boolean; prior: number | null; post: number; diff: number | null; wPrior: number | null; wBl: number | null; wChosen: number };

function PriorPosterior({ d }: { d: OptimizeOut }) {
  const e = d.expected_returns;
  const rf = Number(e.params.risk_free ?? 0);
  const rows = useMemo<PPRow[]>(() => {
    const touched = new Set((e.views ?? []).flatMap((v) => [v.long, v.short ?? ""]));
    return sortBy(d.universe.tickers, e.mu).map((k) => {
      const prior = e.prior_excess?.[k] != null ? e.prior_excess[k] + rf : null;
      return { ticker: k, view: touched.has(k), prior, post: e.mu[k], diff: prior == null ? null : e.mu[k] - prior, wPrior: e.market_weights?.[k] ?? null, wBl: e.unconstrained_bl_weights?.[k] ?? null, wChosen: d.result.weights[k] ?? 0 };
    });
  }, [d, e, rf]);
  const cols: Column<PPRow>[] = [
    { key: "ticker", label: "Asset", render: (r) => <span className={`num ${r.view ? "op-strong" : ""}`}>{r.view ? `${r.ticker} ·V` : r.ticker}</span> },
    { key: "prior", label: sym("μ prior"), numeric: true, format: (v) => fmtPct(v, 2, { signed: true }), info: INFO.blPosterior },
    { key: "post", label: sym("μ post"), numeric: true, format: (v) => fmtPct(v, 2, { signed: true }) },
    { key: "diff", label: "Δ", numeric: true, format: (v) => fmtPct(v, 2, { signed: true }) },
    { key: "wPrior", label: "W prior", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 600 },
    { key: "wBl", label: "W BL", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 600, title: "Unconstrained BL portfolio (δΣ)⁻¹μ" },
    { key: "wChosen", label: `W ${METHOD_CODE[d.result.method]}`, numeric: true, format: (v) => fmtPct(v, 1) },
  ];
  return (
    <>
      <DataTable<PPRow> columns={cols} rows={rows} rowKey={(r) => r.ticker} />
      <Readline
        items={[
          { k: "RF", v: fmtPct(rf, 2) },
          { k: "μ", v: "EXCESS + RF" },
          { k: "·V", v: "NAMED IN A VIEW" },
        ]}
      />
    </>
  );
}

function ViewsTable({ d }: { d: OptimizeOut }) {
  const rows = useMemo(() => viewRows(d.expected_returns), [d]);
  if (!rows.length) return <Absent reason="NO VIEWS WITH CONF > 0" source="POSTERIOR = PRIOR" />;
  const cols: Column<ViewRow>[] = [
    { key: "label", label: "View", render: (r) => <span className="num">{r.label}</span> },
    { key: "confidence", label: "Conf", numeric: true, format: (v) => fmtPct(v, 0), info: INFO.blConfidence },
    { key: "prior_implied", label: "Prior", numeric: true, format: (v) => fmtPct(v, 2, { signed: true }), info: INFO.priorImplied },
    { key: "value", label: "View", numeric: true, format: (v) => fmtPct(v, 2, { signed: true }) },
    { key: "posterior", label: "Post", numeric: true, format: (v) => fmtPct(v, 2, { signed: true }) },
    { key: "pull", label: "Pull", numeric: true, format: (v) => fmtPct(v, 0), heat: { min: 0, max: 1, diverging: false }, info: { title: "Pull", text: "Share of the prior-to-view distance the posterior travelled: 0% ignored, 100% adopted." } },
    { key: "omega", label: "Ω", numeric: true, format: (v) => fmtNum(v, 5), hideBelow: 900, info: { title: "View uncertainty Ω", text: "Variance of the view's error term implied by its confidence (Idzorek).", reference: "Idzorek (2005)" } },
  ];
  return <DataTable<ViewRow> columns={cols} rows={rows} rowKey={(r) => r.label} />;
}
