/**
 * The input rail for the selected rule: its parameters (defaulted to the paper's), the
 * universe, the window and benchmark, execution and costs. Edits are a draft; RUN commits
 * them. Caps labels only; definitions live in Methodology behind the Note markers and in the
 * metric InfoTips.
 */
import type { ReactNode } from "react";
import { DateRangePicker, Field, NumberField, ParamForm, SegmentedControl, Select, Slider, TickerChips, TickerInput, Toggle } from "../../components";
import { Note } from "../../design";
import { fmtPct } from "../../lib/format";
import type { Portfolio } from "../../lib/portfolio";
import { humanize, type LabConfig } from "./config";
import type { Availability } from "./shared";
import type { Catalog, StrategySpec } from "./types";

type Patch = (p: Partial<LabConfig>) => void;

export function SetupRail({ spec, catalog, cfg, set, avail, portfolio, onReset }: { spec: StrategySpec; catalog: Catalog; cfg: LabConfig; set: Patch; avail: Availability; portfolio: Portfolio; onReset: () => void }) {
  const formParams = spec.params.filter((p) => p.type !== "ticker" && p.type !== "weights");
  const tickerParams = spec.params.filter((p) => p.type === "ticker");
  const weightParams = spec.params.filter((p) => p.type === "weights");
  const setParam = (k: string, v: unknown) => set({ params: { ...cfg.params, [k]: v } });
  const ok = cfg.tickers.filter((t) => avail.status(t) !== "missing");
  return (
    <aside className="sl-rail" aria-label="Backtest inputs">
      <RailGroup title="Rule" count={spec.params.length ? `${spec.params.length} PARAMS` : "NO PARAMS"}>
        {formParams.length > 0 && <ParamForm params={formParams} values={cfg.params} onChange={(v) => set({ params: { ...cfg.params, ...v } })} columns={1} />}
        {tickerParams.map((p) => (
          <Field key={p.name} label={humanize(p.name)}>
            <Select value={String(cfg.params[p.name] ?? "")} onChange={(v) => setParam(p.name, v)} options={[{ value: "", label: "CASH · T-BILLS" }, ...cfg.tickers.map((t) => ({ value: t, label: t }))]} ariaLabel={p.name} />
          </Field>
        ))}
        {weightParams.map((p) => (
          <WeightsEditor key={p.name} tickers={cfg.tickers} value={(cfg.params[p.name] ?? {}) as Record<string, number>} onChange={(w) => setParam(p.name, w)} portfolio={portfolio} />
        ))}
        <div className="sl-rail-row">
          <button type="button" className="btn btn-sm" onClick={onReset}>
            PAPER DEFAULTS
          </button>
        </div>
      </RailGroup>

      <RailGroup title="Universe" count={`${ok.length}`}>
        <UniverseEditor spec={spec} catalog={catalog} cfg={cfg} set={set} avail={avail} portfolio={portfolio} />
      </RailGroup>

      <RailGroup title="Window">
        <Field label="Live window">
          <DateRangePicker value={{ start: cfg.start, end: cfg.end }} onChange={({ start, end }) => set({ start, end })} anchor={avail.asOf} presets={["3Y", "5Y", "10Y", "Max"]} />
        </Field>
        <Field label="Benchmark">
          <div className="sl-bench">
            <span className="oc-chip">
              <span className="num">{cfg.benchmark}</span>
            </span>
            <TickerInput placeholder="CHANGE" onSelect={(t) => set({ benchmark: t })} exclude={[cfg.benchmark]} />
          </div>
          {avail.status(cfg.benchmark) === "missing" && <div className="sl-flag loss num">NO DATA · {cfg.benchmark}</div>}
        </Field>
      </RailGroup>

      <RailGroup
        title={
          <>
            Execution
            <Note n={2} to="bt-engine" />
          </>
        }
      >
        <div className="sl-pair">
          <NumberField label="Cost" ariaLabel="Trading cost, basis points one way" value={cfg.cost_bps} unit="bp" min={0} max={500} step={1} onChange={(v) => set({ cost_bps: v })} />
          <NumberField label="Borrow" ariaLabel="Borrow fee, basis points a year" value={cfg.borrow_bps} unit="bp/y" min={0} max={5000} step={5} onChange={(v) => set({ borrow_bps: v })} />
        </div>
        <Field label="Lag">
          <SegmentedControl size="sm" ariaLabel="Execution lag" options={["1", "2", "3", "5"].map((v) => ({ value: v, label: `T+${v}` }))} value={String(cfg.execution_lag)} onChange={(v) => set({ execution_lag: Number(v) })} />
        </Field>
        <Field label="Rebalance">
          <Select value={cfg.rebalance ?? ""} onChange={(v) => set({ rebalance: v || null })} ariaLabel="Rebalance" options={[{ value: "", label: `DEFAULT · ${spec.default_rebalance.toUpperCase()}` }, ...catalog.rebalance_choices.map((r) => ({ value: r, label: humanize(r) }))]} />
        </Field>
        <Toggle label="VOL TARGET" checked={cfg.vol_target !== null} onChange={(on) => set({ vol_target: on ? 0.1 : null })} />
        {cfg.vol_target !== null && <Slider value={cfg.vol_target} min={0.02} max={0.4} step={0.01} format={(v) => `${fmtPct(v, 0)} · ANN`} onChange={(v) => set({ vol_target: v })} />}
      </RailGroup>
    </aside>
  );
}

function RailGroup({ title, count, children }: { title: ReactNode; count?: string; children: ReactNode }) {
  return (
    <section className="sl-rail-group">
      <h2 className="sl-rail-title">
        <span>{title}</span>
        {count !== undefined && <span className="sl-rail-count num">{count}</span>}
      </h2>
      <div className="sl-rail-body">{children}</div>
    </section>
  );
}

function UniverseEditor({ spec, catalog, cfg, set, avail, portfolio }: { spec: StrategySpec; catalog: Catalog; cfg: LabConfig; set: Patch; avail: Availability; portfolio: Portfolio }) {
  const ok = cfg.tickers.filter((t) => avail.status(t) !== "missing");
  const missing = cfg.tickers.filter((t) => avail.status(t) === "missing");
  const presets = spec.suggested_universes.map((k) => catalog.universes.find((u) => u.key === k)).filter((u): u is Catalog["universes"][number] => !!u);
  const pfTickers = [...new Set(portfolio.holdings.filter((h) => h.weight !== 0).map((h) => h.ticker))];
  return (
    <>
      <TickerChips tickers={ok} addable onChange={(ts) => set({ tickers: [...ts, ...missing.filter((m) => !ts.includes(m))] })} />
      {missing.length > 0 && (
        <div className="sl-missing num">
          <span className="sl-missing-k">NO DATA · LEFT OUT</span>
          {missing.map((t) => (
            <button key={t} type="button" title={avail.reason(t)} aria-label={`Remove ${t}`} onClick={() => set({ tickers: cfg.tickers.filter((x) => x !== t) })}>
              {t}
            </button>
          ))}
        </div>
      )}
      {ok.length < spec.min_assets && !avail.loading && <div className="sl-flag loss num">MIN {spec.min_assets} ASSETS WITH DATA</div>}
      <div className="sl-rail-row">
        {pfTickers.length > 0 && (
          <button type="button" className="btn btn-sm" onClick={() => set({ tickers: pfTickers })} title={pfTickers.join(", ")}>
            USE PORT
          </button>
        )}
        <Select<string>
          ariaLabel="Universe presets"
          value=""
          onChange={(k) => {
            if (k === "__paper") set({ tickers: [...spec.default_tickers] });
            const u = presets.find((p) => p.key === k);
            if (u) set({ tickers: [...u.tickers] });
          }}
          options={[{ value: "", label: "PRESET" }, { value: "__paper", label: `PAPER DEFAULT · ${spec.default_tickers.length}` }, ...presets.map((u) => ({ value: u.key, label: `${(u.label ?? humanize(u.key)).toUpperCase()} · ${u.tickers.length}` }))]}
        />
      </div>
    </>
  );
}

function WeightsEditor({ tickers, value, onChange, portfolio }: { tickers: string[]; value: Record<string, number>; onChange: (w: Record<string, number>) => void; portfolio: Portfolio }) {
  const total = tickers.reduce((s, t) => s + (value[t] ?? 0), 0);
  const empty = Object.values(value).every((v) => !v);
  const fromPortfolio = () => {
    const w: Record<string, number> = {};
    for (const h of portfolio.holdings) if (tickers.includes(h.ticker)) w[h.ticker] = (w[h.ticker] ?? 0) + h.weight;
    onChange(w);
  };
  return (
    <Field label="Target weights" hint={<span className="num">{empty ? "BLANK = 1/N" : `Σ ${fmtPct(total, 1)}${total < 0.999 ? ` · CASH ${fmtPct(1 - total, 1)}` : ""}`}</span>}>
      <table className="sl-weights num">
        <tbody>
          {tickers.map((t) => (
            <tr key={t}>
              <th scope="row">{t}</th>
              <td>
                <NumberField ariaLabel={`Weight ${t}`} value={value[t] ?? 0} percent min={-2} max={3} step={0.05} onChange={(v) => onChange({ ...value, [t]: v })} width={110} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="sl-rail-row">
        <button type="button" className="btn btn-sm" onClick={() => onChange({})}>
          1/N
        </button>
        {portfolio.holdings.some((h) => tickers.includes(h.ticker)) && (
          <button type="button" className="btn btn-sm" onClick={fromPortfolio}>
            FROM PORT
          </button>
        )}
      </div>
    </Field>
  );
}
