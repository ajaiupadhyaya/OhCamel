/**
 * DCF — POST /api/fundamentals/{t}/dcf: a two-stage FCFF valuation.
 *
 * The server derives every input from data (POST {} → `inputs[*]` with source + method); those
 * pre-fill the assumption strip, each with its source. Any override is sent on REVALUE with only
 * the overridden fields. Results lead with value per share against the price, then the EV bridge
 * (a ruled table with proportional bars), the WACC × terminal-growth grid (cells under the price
 * in signal, the base case boxed), the reverse DCF and the projection.
 */
import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { DataTable, InfoTip, NumberField, Panel, SegmentedControl, Slider, StatGrid, StatTile, Toggle, type Column } from "../../components";
import { Note } from "../../design";
import type { Info } from "../../lib/glossary";
import { EM_DASH, fmtDate, fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
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
  initial_growth: { key: "initial_growth", label: "G · YEAR 1", min: -0.2, max: 0.4, step: 0.0025, fmt: p2, info: INFO.initial_growth },
  terminal_growth: { key: "terminal_growth", label: "G · TERMINAL", min: -0.01, max: 0.05, step: 0.0005, fmt: p2, info: INFO.terminal_growth },
  risk_free: { key: "risk_free", label: "RISK-FREE", min: 0, max: 0.08, step: 0.0005, fmt: p2, info: INFO.risk_free },
  beta: { key: "beta", label: "BETA", min: 0, max: 3, step: 0.01, fmt: (v) => fmtNum(v, 2), info: INFO.beta },
  erp: { key: "erp", label: "ERP", min: 0.02, max: 0.1, step: 0.0005, fmt: p2, info: INFO.erp },
  cost_of_debt: { key: "cost_of_debt", label: "COST OF DEBT", min: 0, max: 0.15, step: 0.0025, fmt: p2, info: INFO.cost_of_debt },
  tax_rate: { key: "tax_rate", label: "TAX RATE", min: 0, max: 0.5, step: 0.005, fmt: (v) => fmtPct(v, 1), info: INFO.tax_rate },
  wacc: { key: "wacc", label: "WACC", min: 0.03, max: 0.2, step: 0.0005, fmt: p2, info: INFO.wacc },
};

const day = (d: string | null | undefined) => fmtDate(d, "short-year").toUpperCase();

/** One terse caps line saying where a derived input came from. */
export function sourceLine(key: string, inp: DcfInput | undefined): ReactNode {
  if (!inp) return null;
  if (inp.source === "unavailable") return <span className="loss">UNAVAILABLE · {inp.method ?? "SET A VALUE"}</span>;
  if (inp.source === "override") return "OVERRIDE";
  const n = (k: string) => inp[k] as number | undefined;
  const s = (k: string) => inp[k] as string | undefined;
  switch (key) {
    case "risk_free":
      return <>FRED DGS10{s("as_of") ? ` · ${day(s("as_of"))}` : ""}</>;
    case "terminal_growth":
      return <>FRED T10YIE{s("as_of") ? ` · ${day(s("as_of"))}` : ""}</>;
    case "initial_growth": {
      const h = n("historical_revenue_cagr");
      const clipped = h != null && inp.value != null && Math.abs(h - inp.value) > 1e-9;
      return <>{n("cagr_years")}Y REV CAGR {fmtPct(h, 1)}{clipped ? ` · CLIPPED ${fmtPct(inp.value, 1)}` : ""}</>;
    }
    case "beta":
      return <>{s("type") === "blume" ? "BLUME" : "OLS"} · {n("n_months")}M VS {s("benchmark") ?? "SPY"} · R² {fmtNum(n("r2"), 2)} · SE {fmtNum(n("se"), 2)}</>;
    case "erp": {
      const start = s("start")?.slice(0, 4);
      const end = s("end")?.slice(0, 4);
      return <>{s("data")?.startsWith("Ken French") ? "FRENCH MKT−RF" : (s("data") ?? "HIST EXCESS RETURN").toUpperCase()}{start && end ? ` · ${start}–${end}` : ""} · SE {fmtPct(n("se"), 1)}</>;
    }
    case "cost_of_debt":
      return <>{(inp.method ?? "").toUpperCase()}</>;
    case "tax_rate":
      return <>MEAN EFFECTIVE · 3 FY · 10-K</>;
    case "wacc":
      return <>E {fmtPct(n("equity_weight"), 1)} MKT · D {fmtPct(n("debt_weight"), 1)} BOOK</>;
    case "base_fcff":
      return <>{(s("label") ?? "").toUpperCase()} · CFO {money(n("cfo"))} + INT(1−T) − CAPEX {money(n("capex"))}</>;
    default:
      return inp.method ? inp.method.toUpperCase() : null;
  }
}

export function DcfTab({ ticker }: { ticker: string }) {
  const base = useDcf(ticker, {});
  const [applied, setApplied] = useState<DcfBody>({});
  const [draft, setDraft] = useState<DcfBody>({});
  const run = useDcf(ticker, applied);

  const pending = useMemo(() => (Object.keys(draft) as (keyof DcfBody)[]).filter((k) => draft[k] !== applied[k]), [draft, applied]);
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
        title={
          <>
            DCF · FCFF · TWO-STAGE
            <Note n={1} to="dcf" />
          </>
        }
        query={base}
        skeletonHeight={460}
      />
    );

  const waccOverride = (draft.wacc ?? applied.wacc) !== undefined;
  const years = draft.years ?? applied.years ?? 5;
  const basis = ("fcff_basis" in draft ? draft.fcff_basis : applied.fcff_basis) ?? "auto";
  const betaType = ("beta_type" in draft ? draft.beta_type : applied.beta_type) ?? "raw";
  const betaDerived = derived?.beta;
  const baseFcff = (draft.base_fcff ?? applied.base_fcff ?? derived?.base_fcff?.value ?? null) as number | null;
  const fcffOver = (draft.base_fcff ?? applied.base_fcff) !== undefined;

  const numRow = (k: NumKey, disabled = false) => {
    const sp = SPECS[k];
    const v = val(k);
    const d = derived?.[k]?.value;
    const lo = Math.min(sp.min, v ?? sp.min, d ?? sp.min);
    const hi = Math.max(sp.max, v ?? sp.max, d ?? sp.max);
    const isOver = draft[k] !== undefined || applied[k] !== undefined;
    return (
      <div key={k} className={`co-assump ${disabled ? "disabled" : ""} ${isOver ? "over" : ""}`}>
        <Slider label={sp.label} info={sp.info} value={v ?? (lo + hi) / 2} min={lo} max={hi} step={sp.step} format={(x) => (v == null ? EM_DASH : sp.fmt(x))} onChange={(x) => set({ [k]: x } as DcfBody)} disabled={disabled} />
        <div className="co-assump-src num">
          {isOver ? (
            <>
              <span className="co-tag on">OVR</span>
              <span>DATA {d != null ? sp.fmt(d) : EM_DASH}</span>
              <button type="button" className="co-reset" onClick={() => reset(k)}>
                RESET
              </button>
            </>
          ) : (
            <>
              <span className={`co-tag ${derived?.[k]?.source === "override" ? "on" : ""}`}>{derived?.[k]?.source === "override" ? "OVR" : "DATA"}</span>
              <span>{sourceLine(k, derived?.[k])}</span>
            </>
          )}
        </div>
      </div>
    );
  };

  const ke = derived?.cost_of_equity?.value;
  const liveKe = !waccOverride && val("risk_free") != null && val("beta") != null && val("erp") != null ? (val("risk_free") as number) + (val("beta") as number) * (val("erp") as number) : null;
  const tag = (asOf?: string) => (asOf ? ` · ${day(asOf)}` : "");

  return (
    <div className="co-dcf">
      <aside className="co-dcf-side">
        <Panel
          title="ASSUMPTIONS"
          notes={[]}
          provenance={[]}
          actions={
            overridden || pending.length ? (
              <button type="button" className="btn btn-sm" onClick={resetAll}>
                ALL TO DATA
              </button>
            ) : undefined
          }
        >
          <div className="co-assump-group">
            <h4 className="co-sub">CASH FLOW</h4>
            <div className="co-assump">
              <div className="oc-slider-head">
                <span className="oc-field-label">
                  BASE FCFF <InfoTip info={INFO.base_fcff} size={12} label="Base FCFF" />
                </span>
                <SegmentedControl
                  size="sm"
                  ariaLabel="Base period"
                  options={[
                    { value: "auto", label: "AUTO", title: "TTM when four fresh quarters exist, else the last fiscal year" },
                    { value: "ttm", label: "TTM" },
                    { value: "fy", label: "FY" },
                  ]}
                  value={basis}
                  onChange={(b) => set({ fcff_basis: b === "auto" ? undefined : b })}
                />
              </div>
              <div className="co-fcff-row">
                <NumberField ariaLabel="Base FCFF in billions of dollars" value={baseFcff != null ? baseFcff / 1e9 : null} onChange={(x) => set({ base_fcff: x * 1e9 })} unit="$BN" step={0.1} digits={2} width={140} />
                {fcffOver && (
                  <button type="button" className="co-reset" onClick={() => reset("base_fcff")}>
                    RESET
                  </button>
                )}
              </div>
              <div className="co-assump-src num">
                <span className={`co-tag ${fcffOver ? "on" : ""}`}>{fcffOver ? "OVR" : "DATA"}</span>
                <span>{sourceLine("base_fcff", derived?.base_fcff)}</span>
              </div>
            </div>
            <div className="co-assump">
              <Slider label="FORECAST YEARS" info={INFO.years} value={years} min={3} max={15} step={1} format={(x) => `${x}Y`} onChange={(x) => set({ years: x })} />
            </div>
          </div>

          <div className="co-assump-group">
            <h4 className="co-sub">GROWTH</h4>
            {numRow("initial_growth")}
            {numRow("terminal_growth")}
          </div>

          <div className="co-assump-group">
            <div className="co-sub co-sub-row">
              <span>DISCOUNT RATE</span>
              <Toggle
                label="SET WACC"
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
                    { value: "raw", label: `RAW ${fmtNum(betaDerived.raw as number, 2)}` },
                    { value: "blume", label: `BLUME ${fmtNum(betaDerived.blume_adjusted as number, 2)}`, title: "0.67 β + 0.33 (Blume 1971)" },
                  ]}
                  value={betaType}
                  onChange={(b) => set({ beta_type: b === "raw" ? undefined : b })}
                />
              </div>
            )}
            {numRow("erp", waccOverride)}
            <div className={`co-derived ${waccOverride ? "disabled" : ""}`}>
              <span>
                COST OF EQUITY <InfoTip info={INFO.cost_of_equity} size={12} label="Cost of equity" />
              </span>
              <span className="num">{fmtPct(liveKe ?? ke, 2)}</span>
            </div>
            {numRow("cost_of_debt", waccOverride)}
            {numRow("tax_rate")}
            {waccOverride ? (
              numRow("wacc")
            ) : (
              <div className="co-derived">
                <span>
                  WACC <InfoTip info={INFO.wacc} size={12} label="WACC" />
                </span>
                <span className="num">{fmtPct(run.data?.inputs.wacc?.value ?? derived?.wacc?.value, 2)}</span>
              </div>
            )}
          </div>

          <div className="co-run">
            <button type="button" className="btn btn-primary" disabled={!pending.length || run.isFetching} onClick={apply}>
              {run.isFetching ? "VALUING" : pending.length ? `REVALUE · ${pending.length}` : "UP TO DATE"}
            </button>
            <span className="co-ctl-k">{pending.length ? "PENDING" : overridden ? "SCENARIO" : "BASE CASE · DATA"}</span>
          </div>
        </Panel>
      </aside>

      <div className="co-dcf-main stack">
        <Panel<DcfOut>
          title={
            <>
              VALUE / SHARE · FCFF · {years}Y
              <Note n={1} to="dcf" />
            </>
          }
          query={run}
          skeletonHeight={200}
          notes={[]}
          provenance={[]}
          asOf={run.data?.price_as_of ?? undefined}
        >
          {(d) => <ValueHead d={d} />}
        </Panel>
        {run.data && !run.isError && (
          <>
            <div className="grid-2">
              <Panel title={`EV BRIDGE · PV${tag(run.data.price_as_of ?? undefined)}`} flush notes={[]} provenance={[]}>
                <Bridge d={run.data} />
              </Panel>
              <Panel title="VALUE / SHARE · WACC × G TERMINAL" flush notes={[]} provenance={[]} asOf={run.data.price_as_of ?? undefined}>
                <Sensitivity d={run.data} />
              </Panel>
            </div>
            <Panel
              title={
                <>
                  REVERSE DCF · IMPLIED G
                  <Note n={2} to="reverse-dcf" />
                </>
              }
              notes={[]}
              provenance={[]}
              asOf={run.data.price_as_of ?? undefined}
            >
              <Reverse d={run.data} />
            </Panel>
            <Panel title="PROJECTION" flush notes={[]} provenance={[]}>
              <Projection d={run.data} />
            </Panel>
            <Panel<DcfOut> title="MODEL · REFERENCES" query={run} skeletonHeight={80}>
              {(d) => (
                <div className="co-refs">
                  <div className="co-ctl-k">
                    {d.method.model.toUpperCase()} · {d.method.years}Y EXPLICIT · END-OF-YEAR DISCOUNTING
                  </div>
                  <ol className="co-ref-list">
                    {d.method.references.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ol>
                </div>
              )}
            </Panel>
          </>
        )}
      </div>
    </div>
  );
}

function ValueHead({ d }: { d: DcfOut }) {
  const v = d.valuation;
  const up = v.upside;
  return (
    <StatGrid min={130}>
      <StatTile label="DCF / SHARE" value={v.value_per_share != null ? `$${fmtNum(v.value_per_share, 2)}` : EM_DASH} info={INFO.dcf} />
      <StatTile label="PRICE" value={`$${fmtNum(d.price, 2)}`} caption={d.price_as_of ? day(d.price_as_of) : undefined} />
      <StatTile label={up != null && up < 0 ? "DOWNSIDE" : "UPSIDE"} value={fmtSignedPct(up, 1)} tone={up != null && up < 0 ? "loss" : "neutral"} />
      <StatTile size="sm" label="EV" value={money(v.enterprise_value)} info={INFO.ev} />
      <StatTile size="sm" label="NET DEBT" value={money(v.net_debt)} caption={`DEBT ${money(v.total_debt)} · CASH ${money(v.cash)}`} />
      <StatTile size="sm" label="EQUITY" value={money(v.equity_value)} caption={`CAP ${money(d.market_cap)}`} />
      <StatTile size="sm" label="TV SHARE" value={v.terminal_share} format={(x) => fmtPct(x, 0)} info={INFO.terminal_share} caption={v.terminal_share != null && v.terminal_share > 0.75 ? "> 75% · PERPETUITY-DRIVEN" : undefined} />
      <StatTile size="sm" label="EXIT MULT" value={`${fmtNum(v.implied_exit_ev_fcff, 1)}×`} caption="TV / FCFF N" info={INFO.exit_multiple} />
      <StatTile size="sm" label="WACC" value={d.inputs.wacc?.value ?? null} format={(x) => fmtPct(x, 2)} info={INFO.wacc} caption={`G TERM ${fmtPct(d.inputs.terminal_growth?.value, 2)}`} />
    </StatGrid>
  );
}

export interface BridgeRow {
  k: string;
  v: number;
  total?: boolean;
}

/** PV of each forecast year, the terminal PV, EV, minus net debt, equity (USD). */
export function bridgeRows(d: DcfOut): BridgeRow[] {
  const v = d.valuation;
  return [...v.table.map((r) => ({ k: `Y${r.year}`, v: r.pv })), { k: "TERMINAL", v: v.pv_terminal }, { k: "EV", v: v.enterprise_value, total: true }, { k: "− NET DEBT", v: -v.net_debt }, { k: "EQUITY", v: v.equity_value, total: true }];
}

function Bridge({ d }: { d: DcfOut }) {
  const rows = bridgeRows(d);
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.v)));
  const cols: Column<BridgeRow>[] = [
    { key: "k", label: "STEP", sortable: false, render: (r) => <span className={r.total ? "co-strong" : ""}>{r.k}</span> },
    { key: "v", label: "PV", numeric: true, sortable: false, render: (r) => <span className={r.total ? "co-strong" : ""}>{money(r.v, 1)}</span> },
    { key: "bar", label: <span className="sr-only">BAR</span>, sortable: false, width: "45%", render: (r) => <span className="co-hbar" aria-hidden><span className={`co-hbar-fill ${r.v < 0 ? "neg" : ""} ${r.total ? "total" : ""}`} style={{ "--w": `${(Math.abs(r.v) / max) * 100}%` } as CSSProperties} /></span> },
  ];
  return <DataTable columns={cols} rows={rows} rowKey={(r) => r.k} />;
}

function Sensitivity({ d }: { d: DcfOut }) {
  const s = d.sensitivity;
  const mid = Math.floor(s.wacc.length / 2);
  const midG = Math.floor(s.terminal_growth.length / 2);
  return (
    <div className="oc-table-wrap" tabIndex={0} role="region" aria-label="Value per share sensitivity">
      <table className="oc-table oc-table-compact co-sens">
        <thead>
          <tr>
            <th>WACC \ G</th>
            {s.terminal_growth.map((g) => (
              <th key={g} className="num">
                {fmtPct(g, 2)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {s.wacc.map((w, i) => (
            <tr key={w}>
              <td className="num">{fmtPct(w, 2)}</td>
              {s.value_per_share[i].map((v, j) => (
                <td key={j} className={`num ${v != null && v < d.price ? "loss" : ""} ${i === mid && j === midG ? "co-sens-base" : ""}`}>
                  {v == null ? EM_DASH : fmtNum(v, 0)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="co-sens-key num">$ / SHARE · RED BELOW PRICE ${fmtNum(d.price, 2)} · BOX = BASE</div>
    </div>
  );
}

interface ProjRow {
  label: string;
  growth: number | null;
  fcff: number | null;
  discount_factor: number | null;
  pv: number | null;
}

function Projection({ d }: { d: DcfOut }) {
  const v = d.valuation;
  const rows: ProjRow[] = [
    ...v.table.map((r) => ({ label: `Y${r.year}`, growth: r.growth, fcff: r.fcff, discount_factor: r.discount_factor, pv: r.pv })),
    { label: "TERMINAL", growth: d.inputs.terminal_growth?.value ?? null, fcff: v.terminal_value, discount_factor: v.table.at(-1)?.discount_factor ?? null, pv: v.pv_terminal },
  ];
  const cols: Column<ProjRow>[] = [
    { key: "label", label: "PERIOD", sortable: false },
    { key: "growth", label: "G", numeric: true, format: (x) => fmtPct(x, 2), sortable: false },
    { key: "fcff", label: "FCFF / TV", numeric: true, format: (x) => money(x, 2), sortable: false },
    { key: "discount_factor", label: "DF", numeric: true, format: (x) => fmtNum(x, 4), sortable: false, hideBelow: 600 },
    { key: "pv", label: "PV", numeric: true, format: (x) => money(x, 2), sortable: false },
  ];
  return (
    <DataTable
      columns={cols}
      rows={rows}
      rowKey={(r) => r.label}
      footer={
        <div className="co-proj-foot num">
          <span className="co-ctl-k">Σ PV = EV</span> <span>{money(v.enterprise_value, 2)}</span>
        </div>
      }
    />
  );
}

function Reverse({ d }: { d: DcfOut }) {
  const r = d.reverse_dcf;
  if (r.implied_growth == null) return <div className="co-none">NOT SOLVED · {(r.reason ?? "no root").toUpperCase()}</div>;
  const hist = r.historical_revenue_cagr;
  const gap = hist != null ? r.implied_growth - hist : null;
  return (
    <StatGrid min={130}>
      <StatTile label="IMPLIED G · FCFF" value={fmtPct(r.implied_growth, 1)} tone={r.implied_growth < 0 ? "loss" : "neutral"} caption={`${r.years}Y THEN ${fmtPct(r.terminal_growth, 1)} · WACC ${fmtPct(r.wacc, 2)}`} info={INFO.reverse_dcf} />
      <StatTile size="sm" label="5Y REV CAGR" value={fmtPct(hist, 1)} />
      <StatTile size="sm" label="IMPLIED − HIST" value={gap == null ? EM_DASH : fmtSignedPct(gap, 1)} caption={gap == null ? undefined : gap > 0 ? "PRICE ASSUMES FASTER" : "PRICE ASSUMES SLOWER"} />
      <StatTile size="sm" label="AT PRICE" value={`$${fmtNum(d.price, 2)}`} caption={`CAP ${money(r.market_cap ?? d.market_cap)}`} />
    </StatGrid>
  );
}
