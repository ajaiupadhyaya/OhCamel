/**
 * The left-hand setup rail: universe, estimation window, estimators, risk-free rate and
 * constraints. Everything here edits the one OptConfig; results re-query as it changes.
 */
import { useMemo, useState, type ReactNode } from "react";
import {
  DateRangePicker,
  Field,
  InfoTip,
  NumberField,
  SegmentedControl,
  Select,
  Slider,
  TickerChips,
  Toggle,
} from "../../components";
import { Icon } from "../../components/Icon";
import { fmtDate, fmtNum, fmtPct, fmtPctPoints } from "../../lib/format";
import { useUniverses } from "../../lib/market";
import { useApiQuery } from "../../lib/query";
import type { Envelope, FramePayload } from "../../lib/types";
import { currentInUniverse, type OptConfig } from "./config";
import { COV_LABEL, INFO, RETURNS_LABEL, covInfo, returnsInfo } from "./info";
import type { CovName, MethodsCatalog, ReturnsModel } from "./types";

type Patch = (p: Partial<OptConfig>) => void;

export function SetupRail({
  cfg,
  set,
  catalog,
  portfolioName,
  onUsePortfolio,
  anchor,
}: {
  cfg: OptConfig;
  set: Patch;
  catalog?: MethodsCatalog;
  portfolioName: string;
  onUsePortfolio: () => void;
  anchor?: string | null;
}) {
  const covRef = (n: CovName) =>
    catalog?.covariance_estimators.find((e) => e.name === n)?.reference;
  const retRef = (n: ReturnsModel) =>
    catalog?.returns_models.find((e) => e.name === n)?.reference;
  return (
    <aside className="op-rail" aria-label="Optimizer inputs">
      <RailGroup
        n="01"
        title="Universe"
        hint="The assets the optimiser may hold."
      >
        <UniverseEditor
          cfg={cfg}
          set={set}
          portfolioName={portfolioName}
          onUsePortfolio={onUsePortfolio}
        />
      </RailGroup>

      <RailGroup
        n="02"
        title="Estimation"
        hint="How the past is turned into forecasts."
      >
        <Field
          label="Window"
          info={{
            text: "Daily returns in this window (common trading days across all assets) feed every estimate below. Longer windows are more precise but slower to adapt.",
          }}
        >
          <DateRangePicker
            value={{ start: cfg.start, end: cfg.end }}
            onChange={({ start, end }) => set({ start, end })}
            anchor={anchor}
            presets={["3Y", "5Y", "10Y", "Max"]}
          />
        </Field>
        <Field
          label="Covariance estimator"
          info={covInfo(cfg.cov, covRef(cfg.cov))}
        >
          <Select<CovName>
            ariaLabel="Covariance estimator"
            value={cfg.cov}
            onChange={(cov) => set({ cov })}
            options={(Object.keys(COV_LABEL) as CovName[]).map((k) => ({
              value: k,
              label: COV_LABEL[k],
            }))}
          />
        </Field>
        {cfg.cov === "ewma" && (
          <Slider
            label="EWMA decay λ"
            value={cfg.ewmaLambda}
            min={0.8}
            max={0.995}
            step={0.005}
            format={(v) =>
              `${fmtNum(v, 3)} · half-life ${fmtNum(Math.log(0.5) / Math.log(v), 0)}d`
            }
            onChange={(ewmaLambda) => set({ ewmaLambda })}
            info={{
              text: "Weight on yesterday's estimate. 0.94 is RiskMetrics' daily choice (half-life ≈ 11 trading days).",
              formula: "h = \\ln 0.5 / \\ln \\lambda",
            }}
          />
        )}
        <Field
          label="Expected returns"
          info={returnsInfo(cfg.returns, retRef(cfg.returns))}
          hint={
            cfg.returns === "capm" || cfg.returns === "black_litterman" ? (
              <>
                Benchmark <span className="num">{cfg.benchmark}</span> (from
                your portfolio settings)
              </>
            ) : undefined
          }
        >
          <Select<ReturnsModel>
            ariaLabel="Expected-return model"
            value={cfg.returns}
            onChange={(returns) => set({ returns })}
            options={(Object.keys(RETURNS_LABEL) as ReturnsModel[]).map(
              (k) => ({ value: k, label: RETURNS_LABEL[k] }),
            )}
          />
        </Field>
        <RiskFree cfg={cfg} set={set} />
      </RailGroup>

      <RailGroup
        n="03"
        title="Constraints"
        hint="Budget Σw = 100 % always holds."
      >
        <Constraints cfg={cfg} set={set} />
      </RailGroup>
    </aside>
  );
}

function RailGroup({
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
    <section className="op-rail-group">
      <header className="op-rail-head">
        <span className="op-rail-n num">{n}</span>
        <div>
          <h3 className="op-rail-title display">{title}</h3>
          {hint && <p className="op-rail-hint">{hint}</p>}
        </div>
      </header>
      <div className="op-rail-body">{children}</div>
    </section>
  );
}

// ------------------------------------------------------------------ universe
function UniverseEditor({
  cfg,
  set,
  portfolioName,
  onUsePortfolio,
}: {
  cfg: OptConfig;
  set: Patch;
  portfolioName: string;
  onUsePortfolio: () => void;
}) {
  const universes = useUniverses();
  const presets = (universes.data ?? []).filter((u) => u.tickers.length >= 2);
  const cur = currentInUniverse(cfg);
  const overlap = cfg.tickers.filter(
    (t) => Math.abs(cfg.current[t] ?? 0) > 1e-9,
  ).length;
  const outside = Object.keys(cfg.current).filter(
    (t) => !cfg.tickers.includes(t) && Math.abs(cfg.current[t]) > 1e-9,
  );
  return (
    <>
      <TickerChips
        tickers={cfg.tickers}
        onChange={(tickers) => set({ tickers })}
        addable
        colored={cfg.tickers.length <= 8}
        max={60}
      />
      {cfg.tickers.length < 2 && (
        <div className="op-warn small">Add at least two assets.</div>
      )}
      <div className="row-wrap">
        <button
          type="button"
          className="btn btn-sm"
          onClick={onUsePortfolio}
          title="Replace the universe with the active portfolio's holdings, and use its weights as the current portfolio"
        >
          <Icon name="portfolio" size={14} /> Use my portfolio
        </button>
        <Select<string>
          ariaLabel="Universe presets"
          className="op-preset"
          value=""
          onChange={(name) => {
            const u = presets.find((p) => p.name === name);
            if (u) set({ tickers: u.tickers.slice(0, 60) });
          }}
          options={[
            {
              value: "",
              label: universes.isLoading
                ? "Loading presets…"
                : "Load a preset…",
            },
            ...presets.map((u) => ({
              value: u.name,
              label: `${u.label} (${u.tickers.length})`,
            })),
          ]}
        />
      </div>
      <div className="op-current small">
        <span className="subtle">Current weights</span>{" "}
        {cur ? (
          <>
            from <b>{cfg.currentSource || portfolioName}</b> ·{" "}
            <span className="num">{overlap}</span> of{" "}
            <span className="num">{cfg.tickers.length}</span> assets held
            {outside.length > 0 && (
              <>
                {" "}
                ·{" "}
                <span className="subtle">
                  {outside.join(", ")} outside the universe (ignored; the rest
                  rescaled to 100 %)
                </span>
              </>
            )}
          </>
        ) : (
          <span className="subtle">
            none of these assets are in {cfg.currentSource || portfolioName} —
            turnover limits and the “current” marker are off.
          </span>
        )}
      </div>
    </>
  );
}

// ------------------------------------------------------------------ risk-free
interface FredOut extends Envelope {
  data: FramePayload;
  summary: Record<string, { latest: number; date: string }>;
}

function RiskFree({ cfg, set }: { cfg: OptConfig; set: Patch }) {
  const fred = useApiQuery<FredOut>(
    "/macro/series",
    { ids: "DGS2", start: cfg.start ?? undefined, end: cfg.end ?? undefined },
    { enabled: cfg.rfMode === "manual", staleTime: 60 * 60_000 },
  );
  const proxy = useMemo(() => {
    const f = fred.data;
    if (!f) return null;
    const ys = (f.data.data["DGS2"] ?? []).filter(
      (v): v is number => v != null && Number.isFinite(v),
    );
    if (!ys.length) return null;
    const s = f.summary?.["DGS2"];
    return {
      latest: s?.latest ?? ys[ys.length - 1],
      date: s?.date,
      mean: ys.reduce((a, b) => a + b, 0) / ys.length,
      from: f.data.index[0] as string,
    };
  }, [fred.data]);
  return (
    <Field label="Risk-free rate" info={INFO.riskFree}>
      <SegmentedControl
        size="sm"
        ariaLabel="Risk-free source"
        options={[
          {
            value: "market",
            label: "3M T-bill (FRED)",
            title: "Window average of FRED DGS3MO, or the Ken French RF series",
          },
          { value: "manual", label: "Set my own" },
        ]}
        value={cfg.rfMode}
        onChange={(rfMode) => set({ rfMode })}
      />
      {cfg.rfMode === "manual" && (
        <div className="op-rf">
          <NumberField
            value={cfg.rf}
            onChange={(rf) => set({ rf })}
            percent
            min={-0.05}
            max={0.5}
            step={0.0025}
            width={110}
          />
          <span className="subtle small">
            per year, constant over the window
          </span>
          {proxy && (
            <div className="op-rf-proxies">
              <span className="subtle small">
                Proxies from FRED DGS2 (2-year Treasury — longer than a bill):
              </span>
              <div className="row-wrap">
                <button
                  type="button"
                  className="op-chip-btn"
                  onClick={() => set({ rf: +(proxy.mean / 100).toFixed(4) })}
                  title={`Average daily DGS2 since ${fmtDate(proxy.from)}`}
                >
                  window avg{" "}
                  <span className="num">{fmtPctPoints(proxy.mean, 2)}</span>
                </button>
                <button
                  type="button"
                  className="op-chip-btn"
                  onClick={() => set({ rf: +(proxy.latest / 100).toFixed(4) })}
                  title={
                    proxy.date ? `DGS2 on ${fmtDate(proxy.date)}` : undefined
                  }
                >
                  latest{" "}
                  <span className="num">{fmtPctPoints(proxy.latest, 2)}</span>
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </Field>
  );
}

// ------------------------------------------------------------------ constraints
function Constraints({ cfg, set }: { cfg: OptConfig; set: Patch }) {
  const [open, setOpen] = useState(Object.keys(cfg.bounds).length > 0);
  const cur = currentInUniverse(cfg);
  const lo = cfg.longOnly ? Math.max(0, cfg.minWeight) : cfg.minWeight;
  const n = cfg.tickers.length;
  const infeasible =
    n > 0 && (cfg.maxWeight * n < 1 - 1e-9 || lo * n > 1 + 1e-9);
  return (
    <>
      <Toggle
        label="Long-only (no short selling)"
        checked={cfg.longOnly}
        onChange={(longOnly) =>
          set({
            longOnly,
            minWeight: longOnly
              ? Math.max(0, cfg.minWeight)
              : cfg.minWeight === 0
                ? -0.2
                : cfg.minWeight,
            maxGross: longOnly ? cfg.maxGross : (cfg.maxGross ?? 1.5),
          })
        }
      />
      <div className="op-pair">
        <NumberField
          label="Min per asset"
          value={lo}
          percent
          min={cfg.longOnly ? 0 : -2}
          max={1}
          step={0.01}
          onChange={(minWeight) => set({ minWeight })}
          info={{
            text: "Lowest weight any single asset may have. Negative values allow shorting (switch off long-only).",
          }}
        />
        <NumberField
          label="Max per asset"
          value={cfg.maxWeight}
          percent
          min={0.01}
          max={3}
          step={0.01}
          onChange={(maxWeight) => set({ maxWeight })}
          info={{
            text: "Cap on any single position — the simplest guard against an optimiser piling into one asset.",
          }}
        />
      </div>
      {infeasible && (
        <div className="op-warn small">
          With {n} assets these caps cannot add up to 100 % — the optimiser will
          reject them.
        </div>
      )}
      {!cfg.longOnly && (
        <NumberField
          label="Max gross leverage"
          value={cfg.maxGross ?? 1.5}
          unit="×"
          min={1}
          max={10}
          step={0.1}
          onChange={(maxGross) => set({ maxGross })}
          info={{
            text: "Limits total long plus total short exposure. 1.5× allows, e.g., 125 % long and 25 % short.",
            formula: "\\sum_i |w_i| \\le L",
          }}
        />
      )}
      <div className="op-turnover">
        <Toggle
          label="Limit turnover vs current"
          checked={cfg.turnoverOn && !!cur}
          disabled={!cur}
          onChange={(turnoverOn) => set({ turnoverOn })}
        />
        {cfg.turnoverOn && cur && (
          <NumberField
            value={cfg.maxTurnover}
            percent
            min={0}
            max={4}
            step={0.05}
            width={96}
            onChange={(maxTurnover) => set({ maxTurnover })}
            info={INFO.turnover}
          />
        )}
        <InfoTip
          info={{
            ...INFO.turnover,
            text: `${INFO.turnover.text} Only methods that take constraints honour it; heuristics (1/N, inverse-vol, ERC, HRP, HERC) ignore it and say so.`,
          }}
          size={12}
        />
      </div>
      <button
        type="button"
        className="op-disclosure"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <Icon name={open ? "chevron-down" : "chevron-right"} size={13} />{" "}
        Per-asset bounds
        {Object.keys(cfg.bounds).length > 0 && (
          <span className="badge accent">
            {
              Object.keys(cfg.bounds).filter((t) => cfg.tickers.includes(t))
                .length
            }
          </span>
        )}
      </button>
      {open && (
        <div className="op-bounds">
          <div className="op-bounds-row op-bounds-headrow subtle">
            <span>Asset</span>
            <span>Min</span>
            <span>Max</span>
            <span />
          </div>
          {cfg.tickers.map((t) => {
            const b = cfg.bounds[t];
            const setB = (v: [number, number] | null) => {
              const nb = { ...cfg.bounds };
              if (v) nb[t] = v;
              else delete nb[t];
              set({ bounds: nb });
            };
            return (
              <div key={t} className={`op-bounds-row ${b ? "active" : ""}`}>
                <span className="num">{t}</span>
                <NumberField
                  value={b ? b[0] : lo}
                  percent
                  min={-2}
                  max={1}
                  step={0.01}
                  onChange={(v) => setB([v, b ? b[1] : cfg.maxWeight])}
                />
                <NumberField
                  value={b ? b[1] : cfg.maxWeight}
                  percent
                  min={0}
                  max={3}
                  step={0.01}
                  onChange={(v) => setB([b ? b[0] : lo, v])}
                />
                {b ? (
                  <button
                    type="button"
                    className="icon-btn"
                    aria-label={`Clear bounds for ${t}`}
                    onClick={() => setB(null)}
                  >
                    <Icon name="x" size={13} />
                  </button>
                ) : (
                  <span />
                )}
              </div>
            );
          })}
          <div className="subtle small">
            Per-asset bounds override the global min/max. Pinned rows are
            highlighted.
          </div>
        </div>
      )}
      <div className="subtle small op-foot">
        Global range <span className="num">{fmtPct(lo, 0)}</span> to{" "}
        <span className="num">{fmtPct(cfg.maxWeight, 0)}</span> per asset
        {!cfg.longOnly && cfg.maxGross ? (
          <>
            {" "}
            · gross ≤ <span className="num">{fmtNum(cfg.maxGross, 1)}×</span>
          </>
        ) : null}
        {cfg.turnoverOn && cur ? (
          <>
            {" "}
            · turnover ≤{" "}
            <span className="num">{fmtPct(cfg.maxTurnover, 0)}</span>
          </>
        ) : null}
      </div>
    </>
  );
}
