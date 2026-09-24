/**
 * The configuration panel for the selected strategy: rule parameters (ParamForm), universe,
 * window & benchmark, execution & costs. Edits are a draft; "Run backtest" commits them.
 */
import type { ReactNode } from "react";
import {
  DateRangePicker,
  Field,
  InfoTip,
  NumberField,
  ParamForm,
  SegmentedControl,
  Select,
  Slider,
  TickerChips,
  TickerInput,
  Toggle,
} from "../../components";
import { Icon } from "../../components/Icon";
import { fmtPct } from "../../lib/format";
import type { Portfolio } from "../../lib/portfolio";
import { INFO } from "./info";
import { humanize, type LabConfig } from "./config";
import type { Availability } from "./shared";
import type { Catalog, StrategySpec } from "./types";

type Patch = (p: Partial<LabConfig>) => void;

export function Setup({
  spec,
  catalog,
  cfg,
  set,
  avail,
  portfolio,
  onReset,
  footer,
}: {
  spec: StrategySpec;
  catalog: Catalog;
  cfg: LabConfig;
  set: Patch;
  avail: Availability;
  portfolio: Portfolio;
  onReset: () => void;
  footer: ReactNode;
}) {
  const formParams = spec.params.filter(
    (p) => p.type !== "ticker" && p.type !== "weights",
  );
  const tickerParams = spec.params.filter((p) => p.type === "ticker");
  const weightParams = spec.params.filter((p) => p.type === "weights");
  const setParam = (k: string, v: unknown) =>
    set({ params: { ...cfg.params, [k]: v } });
  return (
    <section className="oc-panel sl-setup" aria-label="Backtest inputs">
      <div className="sl-setup-grid">
        <Block
          n="01"
          title="Rule"
          hint="The strategy's own parameters, defaulted to the paper's choices."
        >
          {formParams.length > 0 && (
            <ParamForm
              params={formParams}
              values={cfg.params}
              onChange={(v) => set({ params: { ...cfg.params, ...v } })}
              columns={1}
            />
          )}
          {tickerParams.map((p) => (
            <Field
              key={p.name}
              label={humanize(p.name)}
              info={{ title: humanize(p.name), text: p.description ?? "" }}
            >
              <Select
                value={String(cfg.params[p.name] ?? "")}
                onChange={(v) => setParam(p.name, v)}
                options={[
                  { value: "", label: "Cash (T-bills)" },
                  ...cfg.tickers.map((t) => ({ value: t, label: t })),
                ]}
                ariaLabel={p.name}
              />
            </Field>
          ))}
          {weightParams.map((p) => (
            <WeightsEditor
              key={p.name}
              tickers={cfg.tickers}
              value={(cfg.params[p.name] ?? {}) as Record<string, number>}
              onChange={(w) => setParam(p.name, w)}
              description={p.description ?? ""}
              portfolio={portfolio}
            />
          ))}
          {spec.params.length === 0 && (
            <p className="subtle small">
              No parameters: this rule is fully described by its universe.
            </p>
          )}
        </Block>

        <Block
          n="02"
          title="Universe"
          hint={`The assets the rule trades${spec.min_assets > 1 ? ` (at least ${spec.min_assets})` : ""}.`}
        >
          <UniverseEditor
            spec={spec}
            catalog={catalog}
            cfg={cfg}
            set={set}
            avail={avail}
            portfolio={portfolio}
          />
        </Block>

        <Block
          n="03"
          title="Window"
          hint="Look-back history before the start is loaded automatically, so the first signal is on time."
        >
          <Field
            label="Backtest period"
            info={{
              text: "Positions are only taken inside this window. ‘Max’ uses all common history of the universe and benchmark.",
            }}
          >
            <DateRangePicker
              value={{ start: cfg.start, end: cfg.end }}
              onChange={({ start, end }) => set({ start, end })}
              anchor={avail.asOf}
              presets={["3Y", "5Y", "10Y", "Max"]}
            />
          </Field>
          <Field label="Benchmark" info={INFO.benchmark}>
            <div className="sl-bench">
              <span className="oc-chip">
                <span className="num">{cfg.benchmark}</span>
              </span>
              <TickerInput
                className="sl-bench-input"
                placeholder="Change…"
                onSelect={(t) => set({ benchmark: t })}
                exclude={[cfg.benchmark]}
              />
            </div>
            {avail.status(cfg.benchmark) === "missing" && (
              <div className="sl-warn small">
                <Icon name="alert" size={13} /> No data for {cfg.benchmark} from
                any configured source.
              </div>
            )}
          </Field>
        </Block>

        <Block
          n="04"
          title="Execution & costs"
          hint="Assumptions about trading, not market data — set them to match your broker."
        >
          <div className="sl-pair">
            <NumberField
              label="Trading cost"
              info={INFO.cost_bps}
              value={cfg.cost_bps}
              unit="bp"
              min={0}
              max={500}
              step={1}
              onChange={(v) => set({ cost_bps: v })}
            />
            <NumberField
              label="Borrow fee"
              info={INFO.borrow_bps}
              value={cfg.borrow_bps}
              unit="bp/yr"
              min={0}
              max={5000}
              step={5}
              onChange={(v) => set({ borrow_bps: v })}
            />
          </div>
          <Field label="Execution lag" info={INFO.lag}>
            <SegmentedControl
              size="sm"
              ariaLabel="Execution lag"
              options={["1", "2", "3", "5"].map((v) => ({
                value: v,
                label: `t+${v}`,
              }))}
              value={String(cfg.execution_lag)}
              onChange={(v) => set({ execution_lag: Number(v) })}
            />
          </Field>
          <Field label="Rebalance" info={INFO.rebalance}>
            <Select
              value={cfg.rebalance ?? ""}
              onChange={(v) => set({ rebalance: v || null })}
              ariaLabel="Rebalance"
              options={[
                {
                  value: "",
                  label: `Strategy default (${spec.default_rebalance})`,
                },
                ...catalog.rebalance_choices.map((r) => ({
                  value: r,
                  label: humanize(r),
                })),
              ]}
            />
          </Field>
          <div className="sl-voltarget">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <Toggle
                label="Target portfolio volatility"
                checked={cfg.vol_target !== null}
                onChange={(on) => set({ vol_target: on ? 0.1 : null })}
              />
              <InfoTip info={INFO.vol_target} size={12} />
            </div>
            {cfg.vol_target !== null && (
              <Slider
                value={cfg.vol_target}
                min={0.02}
                max={0.4}
                step={0.01}
                format={(v) => `${fmtPct(v, 0)} a year`}
                onChange={(v) => set({ vol_target: v })}
              />
            )}
          </div>
        </Block>
      </div>
      <footer className="sl-setup-foot">
        {footer}
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={onReset}
        >
          <Icon name="refresh" size={13} /> Reset to paper defaults
        </button>
      </footer>
    </section>
  );
}

function Block({
  n,
  title,
  hint,
  children,
}: {
  n: string;
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="sl-block">
      <div className="sl-block-head">
        <span className="sl-block-n num">{n}</span>
        <div>
          <div className="sl-block-title display">{title}</div>
          {hint && <div className="sl-block-hint">{hint}</div>}
        </div>
      </div>
      <div className="sl-block-body">{children}</div>
    </div>
  );
}

function UniverseEditor({
  spec,
  catalog,
  cfg,
  set,
  avail,
  portfolio,
}: {
  spec: StrategySpec;
  catalog: Catalog;
  cfg: LabConfig;
  set: Patch;
  avail: Availability;
  portfolio: Portfolio;
}) {
  const ok = cfg.tickers.filter((t) => avail.status(t) !== "missing");
  const missing = cfg.tickers.filter((t) => avail.status(t) === "missing");
  const presets = spec.suggested_universes
    .map((k) => catalog.universes.find((u) => u.key === k))
    .filter((u): u is Catalog["universes"][number] => !!u);
  const pfTickers = [
    ...new Set(
      portfolio.holdings.filter((h) => h.weight !== 0).map((h) => h.ticker),
    ),
  ];
  const isDefault = cfg.tickers.join() === spec.default_tickers.join();
  return (
    <>
      <TickerChips
        tickers={ok}
        colored
        addable
        onChange={(ts) =>
          set({ tickers: [...ts, ...missing.filter((m) => !ts.includes(m))] })
        }
      />
      {missing.length > 0 && (
        <div className="sl-missing">
          <div className="sl-missing-head small">
            <Icon name="cloud-off" size={13} /> No data from any configured
            source — left out of the run:
          </div>
          <div className="sl-missing-chips">
            {missing.map((t) => (
              <span
                key={t}
                className="sl-missing-chip num"
                title={avail.reason(t)}
              >
                {t}
                <button
                  type="button"
                  aria-label={`Remove ${t}`}
                  onClick={() =>
                    set({ tickers: cfg.tickers.filter((x) => x !== t) })
                  }
                >
                  <Icon name="x" size={11} />
                </button>
              </span>
            ))}
          </div>
        </div>
      )}
      {ok.length < spec.min_assets && !avail.loading && (
        <div className="sl-warn small">
          <Icon name="alert" size={13} /> {spec.name} needs at least{" "}
          {spec.min_assets} assets with data; add {spec.min_assets - ok.length}{" "}
          more.
        </div>
      )}
      <div className="sl-presets">
        <button
          type="button"
          className={`sl-preset ${isDefault ? "active" : ""}`}
          onClick={() => set({ tickers: [...spec.default_tickers] })}
        >
          Paper default
        </button>
        {presets.map((u) => (
          <button
            key={u.key}
            type="button"
            className={`sl-preset ${cfg.tickers.join() === u.tickers.join() ? "active" : ""}`}
            onClick={() => set({ tickers: [...u.tickers] })}
            title={u.tickers.join(", ")}
          >
            {u.label ?? humanize(u.key)}
          </button>
        ))}
        {pfTickers.length > 0 && (
          <button
            type="button"
            className="sl-preset"
            onClick={() => set({ tickers: pfTickers })}
            title={pfTickers.join(", ")}
          >
            <Icon name="portfolio" size={12} />{" "}
            {portfolio.name || "My portfolio"}
          </button>
        )}
      </div>
    </>
  );
}

function WeightsEditor({
  tickers,
  value,
  onChange,
  description,
  portfolio,
}: {
  tickers: string[];
  value: Record<string, number>;
  onChange: (w: Record<string, number>) => void;
  description: string;
  portfolio: Portfolio;
}) {
  const total = tickers.reduce((s, t) => s + (value[t] ?? 0), 0);
  const empty = Object.values(value).every((v) => !v);
  const fromPortfolio = () => {
    const w: Record<string, number> = {};
    for (const h of portfolio.holdings)
      if (tickers.includes(h.ticker))
        w[h.ticker] = (w[h.ticker] ?? 0) + h.weight;
    onChange(w);
  };
  return (
    <Field
      label="Target weights"
      info={{ title: "Target weights", text: description }}
      hint={
        empty
          ? "Blank = equal weight across the universe."
          : `Total ${fmtPct(total, 1)}${total < 0.999 ? ` · ${fmtPct(1 - total, 1)} in cash` : ""}`
      }
    >
      <div className="sl-weights">
        {tickers.map((t) => (
          <div key={t} className="sl-weight-row">
            <span className="num">{t}</span>
            <NumberField
              value={value[t] ?? 0}
              percent
              min={-2}
              max={3}
              step={0.05}
              onChange={(v) => onChange({ ...value, [t]: v })}
              width={110}
            />
          </div>
        ))}
      </div>
      <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap" }}>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => onChange({})}
        >
          Equal weight
        </button>
        {portfolio.holdings.some((h) => tickers.includes(h.ticker)) && (
          <button type="button" className="btn btn-sm" onClick={fromPortfolio}>
            From {portfolio.name || "my portfolio"}
          </button>
        )}
      </div>
    </Field>
  );
}
