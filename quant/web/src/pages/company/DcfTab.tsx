/**
 * Interactive two-stage FCFF DCF.
 *
 * The server derives every input from data (POST {} → `inputs[*]` with source + method).
 * Those defaults pre-fill the assumption sliders; the user drags any of them and presses
 * "Revalue" to re-run the POST with just the overridden fields. Results: value per share
 * vs price, an EV bridge (waterfall), the projection table, a WACC × g sensitivity grid
 * and the reverse DCF (the growth the current price implies).
 */
import { useMemo, useState, type ReactNode } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, HeatmapChart, InfoTip, NumberField, Panel, SegmentedControl, Slider, StatGrid, StatTile, Toggle, type Column } from "../../components";
import { Icon } from "../../components/Icon";
import type { Info } from "../../lib/glossary";
import { fmtDate, fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import type { Tokens } from "../../lib/theme";
import { INFO } from "./info";
import { money, useDcf } from "./shared";
import type { DcfBody, DcfInput, DcfOut } from "./types";

type NumKey = "initial_growth" | "terminal_growth" | "risk_free" | "beta" | "erp" | "cost_of_debt" | "tax_rate" | "wacc";

interface Spec {
  key: NumKey;
  label: string;
  min: number;
  max: number;
  step: number;
  fmt: (v: number) => string;
  info: Info;
}

const p2 = (v: number) => fmtPct(v, 2);
const SPECS: Record<NumKey, Spec> = {
  initial_growth: { key: "initial_growth", label: "Year-1 FCFF growth", min: -0.2, max: 0.4, step: 0.0025, fmt: p2, info: INFO.initial_growth },
  terminal_growth: { key: "terminal_growth", label: "Terminal growth", min: -0.01, max: 0.05, step: 0.0005, fmt: p2, info: INFO.terminal_growth },
  risk_free: { key: "risk_free", label: "Risk-free rate", min: 0, max: 0.08, step: 0.0005, fmt: p2, info: INFO.risk_free },
  beta: { key: "beta", label: "Beta", min: 0, max: 3, step: 0.01, fmt: (v) => fmtNum(v, 2), info: INFO.beta },
  erp: { key: "erp", label: "Equity risk premium", min: 0.02, max: 0.1, step: 0.0005, fmt: p2, info: INFO.erp },
  cost_of_debt: { key: "cost_of_debt", label: "Pre-tax cost of debt", min: 0, max: 0.15, step: 0.0025, fmt: p2, info: INFO.cost_of_debt },
  tax_rate: { key: "tax_rate", label: "Tax rate", min: 0, max: 0.5, step: 0.005, fmt: (v) => fmtPct(v, 1), info: INFO.tax_rate },
  wacc: { key: "wacc", label: "WACC", min: 0.03, max: 0.2, step: 0.0005, fmt: p2, info: INFO.wacc },
};

/** One line saying where a derived input came from. */
function sourceLine(key: string, inp: DcfInput | undefined): ReactNode {
  if (!inp) return null;
  if (inp.source === "unavailable") return <span className="warn">unavailable — {inp.method ?? "set a value to use it"}</span>;
  if (inp.source === "override") return "your override";
  const n = (k: string) => inp[k] as number | undefined;
  const s = (k: string) => inp[k] as string | undefined;
  switch (key) {
    case "risk_free":
      return <>10-year Treasury · FRED DGS10{s("as_of") ? ` · ${fmtDate(s("as_of"))}` : ""}</>;
    case "terminal_growth":
      return <>10-year breakeven inflation · FRED T10YIE{s("as_of") ? ` · ${fmtDate(s("as_of"))}` : ""}</>;
    case "initial_growth": {
      const h = n("historical_revenue_cagr");
      const clipped = h != null && inp.value != null && Math.abs(h - inp.value) > 1e-9;
      return <>{n("cagr_years")}-year revenue CAGR {fmtPct(h, 1)}{clipped ? `, clipped to ${fmtPct(inp.value, 1)}` : ""} · SEC 10-K</>;
    }
    case "beta":
      return <>{s("type") === "blume" ? "Blume-adjusted" : "OLS"} β, {n("n_months")} monthly returns vs {s("benchmark") ?? "SPY"} · R² {fmtNum(n("r2"), 2)} · se {fmtNum(n("se"), 2)}</>;
    case "erp": {
      const start = s("start")?.slice(0, 4);
      const end = s("end")?.slice(0, 4);
      return <>{s("data")?.startsWith("Ken French") ? "Ken French Mkt−RF" : s("data") ?? "historical excess return"}{start && end ? `, ${start}–${end}` : ""} · se {fmtPct(n("se"), 1)}</>;
    }
    case "cost_of_debt":
      return <>{inp.method}</>;
    case "tax_rate":
      return <>mean effective rate, last 3 fiscal years · SEC 10-K</>;
    case "wacc":
      return <>E {fmtPct(n("equity_weight"), 1)} (market) · D {fmtPct(n("debt_weight"), 1)} (book)</>;
    case "base_fcff":
      return <>{s("label")}: CFO {money(n("cfo"))} + interest × (1−t) − capex {money(n("capex"))}</>;
    case "cost_of_equity":
      return <>CAPM: r<sub>f</sub> + β × ERP</>;
    default:
      return inp.method ?? null;
  }
}

export function DcfTab({ ticker, compact }: { ticker: string; compact?: boolean }) {
  const base = useDcf(ticker, {});
  const [applied, setApplied] = useState<DcfBody>({});
  const [draft, setDraft] = useState<DcfBody>({});
  const run = useDcf(ticker, applied);

  const pending = useMemo(() => {
    const keys = Object.keys(draft) as (keyof DcfBody)[];
    return keys.filter((k) => draft[k] !== applied[k]);
  }, [draft, applied]);
  const overridden = Object.keys(applied).length > 0;

  const derived = base.data?.inputs;
  const val = (k: NumKey): number | null => (draft[k] ?? applied[k] ?? derived?.[k]?.value ?? null) as number | null;
  const set = (patch: DcfBody) => setDraft((d) => ({ ...d, ...patch }));
  const reset = (k: keyof DcfBody) => {
    setDraft((d) => {
      const n = { ...d };
      delete n[k];
      return n;
    });
    setApplied((a) => {
      const n = { ...a };
      delete n[k];
      return n;
    });
  };
  const apply = () => {
    const next: DcfBody = { ...applied, ...draft };
    for (const k of Object.keys(next) as (keyof DcfBody)[]) if (next[k] === undefined) delete next[k];
    setApplied(next);
    setDraft({});
  };
  const resetAll = () => {
    setApplied({});
    setDraft({});
  };

  if (base.isError || base.isLoading || !base.data)
    return (
      <Panel
        title="Discounted cash flow"
        subtitle="A two-stage free-cash-flow valuation with every assumption derived from filings and market data, then yours to change."
        info={INFO.dcf}
        query={base}
        compact={compact}
        skeletonHeight={460}
      />
    );

  const waccOverride = (draft.wacc ?? applied.wacc) !== undefined;
  const years = draft.years ?? applied.years ?? 5;
  const basis = ("fcff_basis" in draft ? draft.fcff_basis : applied.fcff_basis) ?? "auto";
  const betaType = ("beta_type" in draft ? draft.beta_type : applied.beta_type) ?? "raw";
  const betaDerived = derived?.beta;
  const baseFcff = (draft.base_fcff ?? applied.base_fcff ?? derived?.base_fcff?.value ?? null) as number | null;

  const numRow = (k: NumKey, disabled = false) => {
    const sp = SPECS[k];
    const v = val(k);
    const d = derived?.[k]?.value;
    const lo = Math.min(sp.min, v ?? sp.min, d ?? sp.min);
    const hi = Math.max(sp.max, v ?? sp.max, d ?? sp.max);
    const isOver = draft[k] !== undefined || applied[k] !== undefined;
    return (
      <div key={k} className={`co-assump ${disabled ? "disabled" : ""} ${isOver ? "over" : ""}`}>
        <Slider label={sp.label} info={sp.info} value={v ?? (lo + hi) / 2} min={lo} max={hi} step={sp.step} format={(x) => (v == null ? "—" : sp.fmt(x))} onChange={(x) => set({ [k]: x } as DcfBody)} disabled={disabled} />
        <div className="co-assump-src">
          {isOver ? (
            <>
              <span className="badge accent">override</span>
              <span className="subtle">data: {d != null ? sp.fmt(d) : "—"}</span>
              <button type="button" className="co-reset" onClick={() => reset(k)} title="Back to the data-derived value">
                <Icon name="refresh" size={12} /> reset
              </button>
            </>
          ) : (
            <>
              <span className="badge">data</span>
              <span className="subtle">{sourceLine(k, derived?.[k])}</span>
            </>
          )}
        </div>
      </div>
    );
  };

  const ke = derived?.cost_of_equity?.value;
  const liveKe = !waccOverride && val("risk_free") != null && val("beta") != null && val("erp") != null ? (val("risk_free") as number) + (val("beta") as number) * (val("erp") as number) : null;

  return (
    <div className="co-dcf">
      <aside className="co-dcf-side">
        <Panel
          title="Assumptions"
          subtitle="Pre-filled from filings and market data — each shows its source. Drag to override, then revalue."
          info={INFO.dcf}
          notes={[]}
          provenance={[]}
          actions={
            overridden || pending.length ? (
              <button type="button" className="btn btn-sm btn-ghost" onClick={resetAll}>
                <Icon name="refresh" size={13} /> All to data
              </button>
            ) : undefined
          }
        >
          <div className="co-assump-group">
            <div className="co-mini-label">Cash flow</div>
            <div className="co-assump">
              <div className="oc-slider-head">
                <span className="oc-field-label">
                  Base FCFF <InfoTip info={INFO.base_fcff} size={12} />
                </span>
                <SegmentedControl
                  size="sm"
                  ariaLabel="Base period"
                  options={[
                    { value: "auto", label: "Auto", title: "TTM when four fresh quarters exist, else the last fiscal year" },
                    { value: "ttm", label: "TTM" },
                    { value: "fy", label: "FY" },
                  ]}
                  value={basis}
                  onChange={(b) => set({ fcff_basis: b === "auto" ? undefined : b })}
                />
              </div>
              <div className="row co-fcff-row">
                <NumberField value={baseFcff != null ? baseFcff / 1e9 : null} onChange={(x) => set({ base_fcff: x * 1e9 })} unit="$bn" step={0.1} digits={2} width={140} />
                {(draft.base_fcff ?? applied.base_fcff) !== undefined && (
                  <button type="button" className="co-reset" onClick={() => reset("base_fcff")}>
                    <Icon name="refresh" size={12} /> reset
                  </button>
                )}
              </div>
              <div className="co-assump-src">
                <span className="badge">{(draft.base_fcff ?? applied.base_fcff) !== undefined ? "override" : "data"}</span>
                <span className="subtle">{sourceLine("base_fcff", derived?.base_fcff)}</span>
              </div>
            </div>
            <div className="co-assump">
              <Slider label="Explicit forecast years" info={INFO.years} value={years} min={3} max={15} step={1} format={(x) => `${x} yrs`} onChange={(x) => set({ years: x })} />
            </div>
          </div>

          <div className="co-assump-group">
            <div className="co-mini-label">Growth</div>
            {numRow("initial_growth")}
            {numRow("terminal_growth")}
          </div>

          <div className="co-assump-group">
            <div className="co-mini-label row" style={{ justifyContent: "space-between" }}>
              <span>Discount rate</span>
              <Toggle
                label="Set WACC directly"
                checked={waccOverride}
                onChange={(on) => {
                  if (on) set({ wacc: (derived?.wacc?.value as number | null) ?? 0.09 });
                  else reset("wacc");
                }}
              />
            </div>
            {numRow("risk_free", waccOverride)}
            {numRow("beta", waccOverride)}
            {draft.beta === undefined && applied.beta === undefined && betaDerived && !waccOverride && (
              <div className="co-beta-type">
                <SegmentedControl
                  size="sm"
                  ariaLabel="Beta type"
                  options={[
                    { value: "raw", label: `Raw ${fmtNum(betaDerived.raw as number, 2)}` },
                    { value: "blume", label: `Blume ${fmtNum(betaDerived.blume_adjusted as number, 2)}`, title: "0.67 β + 0.33 (Blume 1971)" },
                  ]}
                  value={betaType}
                  onChange={(b) => set({ beta_type: b === "raw" ? undefined : b })}
                />
              </div>
            )}
            {numRow("erp", waccOverride)}
            <div className={`co-derived ${waccOverride ? "disabled" : ""}`}>
              <span>
                Cost of equity <InfoTip info={INFO.cost_of_equity} size={12} />
              </span>
              <span className="num">{fmtPct(liveKe ?? ke, 2)}</span>
            </div>
            {numRow("cost_of_debt", waccOverride)}
            {numRow("tax_rate")}
            {waccOverride ? numRow("wacc") : (
              <div className="co-derived">
                <span>
                  WACC <InfoTip info={INFO.wacc} size={12} />
                </span>
                <span className="num">{fmtPct(run.data?.inputs.wacc?.value ?? derived?.wacc?.value, 2)}</span>
              </div>
            )}
          </div>

          <div className="co-run">
            <button type="button" className="btn btn-primary" disabled={!pending.length || run.isFetching} onClick={apply}>
              <Icon name={run.isFetching ? "refresh" : "arrow-right"} size={15} /> {run.isFetching ? "Valuing…" : pending.length ? `Revalue with ${pending.length} change${pending.length > 1 ? "s" : ""}` : "Up to date"}
            </button>
            <div className="subtle small">{pending.length ? "Changes are applied when you revalue." : overridden ? "Showing your scenario." : "Showing the data-derived base case."}</div>
          </div>
        </Panel>
      </aside>

      <div className="co-dcf-main stack-lg">
        <Panel<DcfOut> title="Intrinsic value" subtitle="What the projected cash flows are worth per share today, next to what the market charges." info={INFO.dcf} query={run} skeletonHeight={200} notes={[]} provenance={[]}>
          {(d) => <ValueHero d={d} />}
        </Panel>
        {run.data && !run.isError && (
          <>
            <div className="grid-2">
              <Panel title="From cash flows to value per share" subtitle="Present value of each forecast year, then the terminal value, add to enterprise value; subtracting net debt leaves the equity." info={{ title: "EV bridge", text: "Each bar is a present value (discounted at the WACC). The terminal bar is usually the largest — which is why the terminal assumptions matter so much.", formula: "\\text{Equity} = \\sum PV(F_i) + PV(TV) - (\\text{Debt} - \\text{Cash})" }} notes={[]}>
                <Waterfall d={run.data} />
              </Panel>
              <Panel title="Sensitivity: WACC × terminal growth" subtitle="Value per share if the two assumptions that matter most were a little different. The outlined cell is the base case." info={INFO.sensitivity} notes={[]}>
                <Sensitivity d={run.data} />
              </Panel>
            </div>
            <Panel title="What the price implies" subtitle="The reverse DCF: keep every other assumption, and solve for the growth that makes the DCF equal today's market cap." info={INFO.reverse_dcf} notes={[]}>
              <Reverse d={run.data} />
            </Panel>
            <Panel title="Projection" subtitle="Free cash flow year by year: growth fades linearly from year 1 to the terminal rate, and each year is discounted at the WACC." info={INFO.initial_growth} flush notes={[]}>
              <Projection d={run.data} />
            </Panel>
            <Panel<DcfOut> title="Model & sources" subtitle="Method, references and data notes for this valuation." query={run} skeletonHeight={80}>
              {(d) => (
                <div className="co-refs small">
                  <div>
                    <span className="subtle">Model:</span> {d.method.model}, {d.method.years}-year explicit period, end-of-year discounting.
                  </div>
                  <ul>
                    {d.method.references.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                </div>
              )}
            </Panel>
          </>
        )}
      </div>
    </div>
  );
}

function ValueHero({ d }: { d: DcfOut }) {
  const v = d.valuation;
  const vps = v.value_per_share;
  const up = v.upside;
  const top = Math.max(vps ?? 0, d.price) * 1.08 || 1;
  return (
    <div className="co-hero">
      <div className="co-hero-nums">
        <div>
          <div className="co-mini-label">DCF value / share</div>
          <div className="co-hero-big num">{vps != null ? `$${fmtNum(vps, 2)}` : "—"}</div>
        </div>
        <div>
          <div className="co-mini-label">Price{d.price_as_of ? ` · ${fmtDate(d.price_as_of)}` : ""}</div>
          <div className="co-hero-big num subtle-strong">${fmtNum(d.price, 2)}</div>
        </div>
        <div>
          <div className="co-mini-label">{up != null && up >= 0 ? "Upside" : "Downside"}</div>
          <div className={`co-hero-big num ${up == null ? "" : up >= 0 ? "gain" : "loss"}`}>{fmtSignedPct(up, 1)}</div>
        </div>
      </div>
      <div className="co-vbars" aria-hidden>
        <div className="co-vbar-row">
          <span className="co-vbar-label">Value</span>
          <span className="co-vbar">
            <span className="co-vbar-fill value" style={{ width: `${((vps ?? 0) / top) * 100}%` }} />
          </span>
        </div>
        <div className="co-vbar-row">
          <span className="co-vbar-label">Price</span>
          <span className="co-vbar">
            <span className="co-vbar-fill price" style={{ width: `${(d.price / top) * 100}%` }} />
          </span>
        </div>
      </div>
      <StatGrid min={150}>
        <StatTile label="Enterprise value" value={money(v.enterprise_value)} info={INFO.ev} />
        <StatTile label="Net debt" value={money(v.net_debt)} caption={`debt ${money(v.total_debt)} · cash ${money(v.cash)}`} />
        <StatTile label="Equity value" value={money(v.equity_value)} caption={`vs cap ${money(d.market_cap)}`} />
        <StatTile label="Terminal share" value={v.terminal_share} format={(x) => fmtPct(x, 0)} tone={v.terminal_share != null && v.terminal_share > 0.75 ? "warn" : "neutral"} info={INFO.terminal_share} />
        <StatTile label="Exit multiple" value={`${fmtNum(v.implied_exit_ev_fcff, 1)}×`} caption="TV / final-year FCFF" info={INFO.exit_multiple} />
        <StatTile label="WACC" value={d.inputs.wacc?.value ?? null} format={(x) => fmtPct(x, 2)} info={INFO.wacc} caption={`terminal g ${fmtPct(d.inputs.terminal_growth?.value, 2)}`} />
      </StatGrid>
    </div>
  );
}

function Waterfall({ d }: { d: DcfOut }) {
  const v = d.valuation;
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const x = [...v.table.map((r) => `Y${r.year}`), "Terminal", "EV", "Net debt", "Equity"];
      const y = [...v.table.map((r) => r.pv / 1e9), v.pv_terminal / 1e9, 0, -v.net_debt / 1e9, 0];
      const measure = [...v.table.map(() => "relative"), "relative", "total", "relative", "total"];
      return [
        {
          type: "waterfall",
          x,
          y,
          measure,
          connector: { line: { color: t.ruleStrong, width: 1, dash: "dot" } },
          increasing: { marker: { color: t.categorical[0] } },
          decreasing: { marker: { color: t.loss } },
          totals: { marker: { color: t.text2 } },
          hovertemplate: "%{x}: <b>$%{y:,.1f}B</b><extra></extra>",
          textposition: "none",
        } as any,
      ];
    },
    [v],
  );
  const layout = useMemo(() => ({ showlegend: false, xaxis: { type: "category", showspikes: false }, yaxis: { tickprefix: "$", ticksuffix: "B", tickformat: ",.0f" }, margin: { l: 56, r: 8, t: 8, b: 32 } }) as any, []);
  return <Chart data={data} layout={layout} height={300} ariaLabel="EV bridge waterfall" />;
}

function Sensitivity({ d }: { d: DcfOut }) {
  const s = d.sensitivity;
  const mid = Math.floor(s.wacc.length / 2);
  const layout = useMemo(
    () =>
      ({
        xaxis: { title: { text: "terminal growth" }, side: "bottom" },
        yaxis: { title: { text: "WACC" } },
        margin: { l: 64, r: 8, t: 8, b: 48 },
        shapes: [{ type: "rect", xref: "x", yref: "y", x0: mid - 0.5, x1: mid + 0.5, y0: mid - 0.5, y1: mid + 0.5, line: { color: "var(--text)", width: 1.5 } }],
      }) as any,
    [mid],
  );
  return (
    <HeatmapChart
      x={s.terminal_growth.map((g) => fmtPct(g, 2))}
      y={s.wacc.map((w) => fmtPct(w, 2))}
      z={s.value_per_share}
      format="usd"
      digits={0}
      diverging
      zmid={d.price}
      showValues
      colorbar={false}
      height={300}
      layout={layout}
    />
  );
}

interface ProjRow {
  year: number;
  growth: number | null;
  fcff: number | null;
  discount_factor: number | null;
  pv: number | null;
  label: string;
}

function Projection({ d }: { d: DcfOut }) {
  const v = d.valuation;
  const rows: ProjRow[] = [
    ...v.table.map((r) => ({ ...r, label: `Year ${r.year}` })),
    { year: v.table.length + 1, label: "Terminal (Gordon)", growth: d.inputs.terminal_growth?.value ?? null, fcff: v.terminal_value, discount_factor: v.table.at(-1)?.discount_factor ?? null, pv: v.pv_terminal },
  ];
  const cols: Column<ProjRow>[] = [
    { key: "label", label: "Period", sortable: false },
    { key: "growth", label: "Growth", numeric: true, format: (x) => fmtPct(x, 2), sortable: false },
    { key: "fcff", label: "FCFF / TV", numeric: true, format: (x) => money(x, 2), sortable: false },
    { key: "discount_factor", label: "Discount factor", numeric: true, format: (x) => fmtNum(x, 4), sortable: false, hideBelow: 600 },
    { key: "pv", label: "Present value", numeric: true, format: (x) => money(x, 2), sortable: false },
  ];
  return <DataTable columns={cols} rows={rows} rowKey={(r) => r.label} footer={<div className="co-proj-foot small"><span className="subtle">Sum of PVs = enterprise value</span> <span className="num">{money(v.enterprise_value, 2)}</span></div>} />;
}

function Reverse({ d }: { d: DcfOut }) {
  const r = d.reverse_dcf;
  if (r.implied_growth == null) return <div className="small">{r.reason ?? "The implied growth could not be solved."}</div>;
  const hist = r.historical_revenue_cagr;
  const gap = hist != null ? r.implied_growth - hist : null;
  return (
    <div className="co-reverse">
      <div>
        <div className="co-mini-label">Market-implied FCFF growth</div>
        <div className={`co-hero-big num ${r.implied_growth < 0 ? "loss" : ""}`}>{fmtPct(r.implied_growth, 1)}<span className="co-score-of"> / yr</span></div>
      </div>
      <p>
        At <span className="num">${fmtNum(d.price, 2)}</span>, the market is paying for free cash flow growing <strong className="num">{fmtPct(r.implied_growth, 1)}</strong> a year for {r.years} years, then{" "}
        <span className="num">{fmtPct(r.terminal_growth, 1)}</span> forever, discounted at <span className="num">{fmtPct(r.wacc, 2)}</span>.
        {hist != null && (
          <>
            {" "}
            Revenue grew <span className="num">{fmtPct(hist, 1)}</span> a year over the last five fiscal years, so the price assumes growth{" "}
            <strong className={gap != null && gap > 0 ? "loss" : "gain"}>{gap != null && Math.abs(gap) < 0.005 ? "in line with" : gap != null && gap > 0 ? `${fmtPct(gap, 1)} above` : `${fmtPct(Math.abs(gap ?? 0), 1)} below`}</strong> that record.
          </>
        )}
      </p>
    </div>
  );
}
