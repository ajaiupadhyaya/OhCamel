/**
 * The input rail: universe, estimation window, estimators, risk-free rate and constraints.
 * Everything here edits the one OptConfig; results re-query as it changes. Inputs carry caps
 * labels only; the estimators' definitions live in Methodology behind the Note markers.
 */
import { useMemo, useState, type ReactNode } from "react";
import { DateRangePicker, Field, NumberField, SegmentedControl, Select, Slider, TickerChips, Toggle } from "../../components";
import { Note } from "../../design";
import { fmtDate, fmtNum, fmtPct, fmtPctPoints } from "../../lib/format";
import { useUniverses } from "../../lib/market";
import { useApiQuery } from "../../lib/query";
import type { Envelope, FramePayload } from "../../lib/types";
import { currentInUniverse, type OptConfig } from "./config";
import { COV_CODE, RETURNS_CODE } from "./info";
import { sym } from "./shared";
import type { CovName, ReturnsModel } from "./types";

type Patch = (p: Partial<OptConfig>) => void;

export function SetupRail({ cfg, set, portfolioName, onUsePortfolio, anchor }: { cfg: OptConfig; set: Patch; portfolioName: string; onUsePortfolio: () => void; anchor?: string | null }) {
  return (
    <aside className="op-rail" aria-label="Optimizer inputs">
      <RailGroup title="Universe" count={cfg.tickers.length}>
        <UniverseEditor cfg={cfg} set={set} portfolioName={portfolioName} onUsePortfolio={onUsePortfolio} />
      </RailGroup>

      <RailGroup title="Estimation">
        <Field label="Window">
          <DateRangePicker value={{ start: cfg.start, end: cfg.end }} onChange={({ start, end }) => set({ start, end })} anchor={anchor} presets={["3Y", "5Y", "10Y", "Max"]} />
        </Field>
        <Field
          label={
            <>
              Σ · Covariance
              <Note n={2} to="covariance" />
            </>
          }
        >
          <Select<CovName> ariaLabel="Covariance estimator" value={cfg.cov} onChange={(cov) => set({ cov })} options={(Object.keys(COV_CODE) as CovName[]).map((k) => ({ value: k, label: COV_CODE[k] }))} />
        </Field>
        {cfg.cov === "ewma" && (
          <Slider label={sym("EWMA λ")} value={cfg.ewmaLambda} min={0.8} max={0.995} step={0.005} format={(v) => `${fmtNum(v, 3)} · HALF-LIFE ${fmtNum(Math.log(0.5) / Math.log(v), 0)}D`} onChange={(ewmaLambda) => set({ ewmaLambda })} />
        )}
        <Field
          label={
            <>
              {sym("μ · Expected returns")}
              <Note n={3} to={cfg.returns === "black_litterman" ? "black-litterman" : "expected-returns"} />
            </>
          }
          hint={cfg.returns === "capm" || cfg.returns === "black_litterman" ? <span className="num">BENCH {cfg.benchmark}</span> : undefined}
        >
          <Select<ReturnsModel> ariaLabel="Expected-return model" value={cfg.returns} onChange={(returns) => set({ returns })} options={(Object.keys(RETURNS_CODE) as ReturnsModel[]).map((k) => ({ value: k, label: RETURNS_CODE[k] }))} />
        </Field>
        <RiskFree cfg={cfg} set={set} />
      </RailGroup>

      <RailGroup title="Constraints">
        <Constraints cfg={cfg} set={set} />
      </RailGroup>
    </aside>
  );
}

function RailGroup({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <section className="op-rail-group">
      <h2 className="op-rail-title">
        {title}
        {count !== undefined && <span className="op-rail-count num">{count}</span>}
      </h2>
      <div className="op-rail-body">{children}</div>
    </section>
  );
}

// ------------------------------------------------------------------ universe
function UniverseEditor({ cfg, set, portfolioName, onUsePortfolio }: { cfg: OptConfig; set: Patch; portfolioName: string; onUsePortfolio: () => void }) {
  const universes = useUniverses();
  const presets = (universes.data ?? []).filter((u) => u.tickers.length >= 2);
  const cur = currentInUniverse(cfg);
  const held = cfg.tickers.filter((t) => Math.abs(cfg.current[t] ?? 0) > 1e-9).length;
  const outside = Object.keys(cfg.current).filter((t) => !cfg.tickers.includes(t) && Math.abs(cfg.current[t]) > 1e-9);
  const source = (cfg.currentSource || portfolioName).toUpperCase();
  return (
    <>
      <TickerChips tickers={cfg.tickers} onChange={(tickers) => set({ tickers })} addable max={60} />
      {cfg.tickers.length < 2 && <div className="op-flag loss num">MIN 2 ASSETS</div>}
      <div className="op-rail-row">
        <button type="button" className="btn btn-sm" onClick={onUsePortfolio} title="Replace the universe with the active portfolio's holdings and use its weights as current">
          USE PORT
        </button>
        <Select<string>
          ariaLabel="Universe presets"
          className="op-preset"
          value=""
          onChange={(name) => {
            const u = presets.find((p) => p.name === name);
            if (u) set({ tickers: u.tickers.slice(0, 60) });
          }}
          options={[{ value: "", label: universes.isLoading ? "PRESETS …" : "PRESET" }, ...presets.map((u) => ({ value: u.name, label: `${u.label.toUpperCase()} · ${u.tickers.length}` }))]}
        />
      </div>
      <dl className="op-kv num">
        <div>
          <dt>CURRENT</dt>
          <dd>{cur ? source : "NONE IN UNIVERSE"}</dd>
        </div>
        {cur && (
          <div>
            <dt>HELD</dt>
            <dd>
              {held} / {cfg.tickers.length}
            </dd>
          </div>
        )}
        {outside.length > 0 && (
          <div title="Holdings outside the universe are ignored; the rest are rescaled to 100%">
            <dt>IGNORED</dt>
            <dd>{outside.join(" ")}</dd>
          </div>
        )}
      </dl>
    </>
  );
}

// ------------------------------------------------------------------ risk-free
interface FredOut extends Envelope {
  data: FramePayload;
  summary: Record<string, { latest: number; date: string }>;
}

const round4 = (x: number) => Math.round(x * 1e4) / 1e4;

function RiskFree({ cfg, set }: { cfg: OptConfig; set: Patch }) {
  const fred = useApiQuery<FredOut>("/macro/series", { ids: "DGS2", start: cfg.start ?? undefined, end: cfg.end ?? undefined }, { enabled: cfg.rfMode === "manual", staleTime: 60 * 60_000 });
  const proxy = useMemo(() => {
    const f = fred.data;
    if (!f) return null;
    const ys = (f.data.data["DGS2"] ?? []).filter((v): v is number => v != null && Number.isFinite(v));
    if (!ys.length) return null;
    const s = f.summary?.["DGS2"];
    return { latest: s?.latest ?? ys[ys.length - 1], date: s?.date, mean: ys.reduce((a, b) => a + b, 0) / ys.length, from: f.data.index[0] as string };
  }, [fred.data]);
  return (
    <Field label="RF · Risk-free">
      <SegmentedControl
        size="sm"
        ariaLabel="Risk-free source"
        options={[
          { value: "market", label: "3M BILL · FRED", title: "Window average of FRED DGS3MO, or the Ken French RF series" },
          { value: "manual", label: "MANUAL" },
        ]}
        value={cfg.rfMode}
        onChange={(rfMode) => set({ rfMode })}
      />
      {cfg.rfMode === "manual" && (
        <div className="op-rf">
          <NumberField ariaLabel="Risk-free rate, annual" value={cfg.rf} onChange={(rf) => set({ rf })} percent min={-0.05} max={0.5} step={0.0025} width={110} />
          <span className="op-rf-unit num">ANN · CONST</span>
          {proxy && (
            <div className="op-rf-proxies num">
              <span className="op-rf-unit">DGS2 · 2Y UST</span>
              <button type="button" className="op-chip-btn" onClick={() => set({ rf: round4(proxy.mean / 100) })} title={`Average daily DGS2 since ${fmtDate(proxy.from)}`}>
                AVG {fmtPctPoints(proxy.mean, 2)}
              </button>
              <button type="button" className="op-chip-btn" onClick={() => set({ rf: round4(proxy.latest / 100) })} title={proxy.date ? `DGS2 on ${fmtDate(proxy.date)}` : undefined}>
                LAST {fmtPctPoints(proxy.latest, 2)}
              </button>
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
  const infeasible = n > 0 && (cfg.maxWeight * n < 1 - 1e-9 || lo * n > 1 + 1e-9);
  const pinned = Object.keys(cfg.bounds).filter((t) => cfg.tickers.includes(t)).length;
  return (
    <>
      <Toggle
        label="LONG ONLY"
        checked={cfg.longOnly}
        onChange={(longOnly) =>
          set({
            longOnly,
            minWeight: longOnly ? Math.max(0, cfg.minWeight) : cfg.minWeight === 0 ? -0.2 : cfg.minWeight,
            maxGross: longOnly ? cfg.maxGross : (cfg.maxGross ?? 1.5),
          })
        }
      />
      <div className="op-pair">
        <NumberField label="MIN · ASSET" value={lo} percent min={cfg.longOnly ? 0 : -2} max={1} step={0.01} onChange={(minWeight) => set({ minWeight })} />
        <NumberField label="MAX · ASSET" value={cfg.maxWeight} percent min={0.01} max={3} step={0.01} onChange={(maxWeight) => set({ maxWeight })} />
      </div>
      {infeasible && (
        <div className="op-flag loss num">
          INFEASIBLE · {n} × BOUNDS ≠ 100%
        </div>
      )}
      {!cfg.longOnly && <NumberField label="GROSS ≤" value={cfg.maxGross ?? 1.5} unit="×" min={1} max={10} step={0.1} onChange={(maxGross) => set({ maxGross })} />}
      <div className="op-turnover">
        <Toggle label="TURNOVER ≤" checked={cfg.turnoverOn && !!cur} disabled={!cur} onChange={(turnoverOn) => set({ turnoverOn })} />
        {cfg.turnoverOn && cur && <NumberField ariaLabel="Maximum turnover" value={cfg.maxTurnover} percent min={0} max={4} step={0.05} width={96} onChange={(maxTurnover) => set({ maxTurnover })} />}
      </div>
      <button type="button" className="op-disclosure" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span aria-hidden>{open ? "−" : "+"}</span> PER-ASSET BOUNDS{pinned > 0 && <span className="num"> · {pinned}</span>}
      </button>
      {open && (
        <table className="op-bounds">
          <thead>
            <tr>
              <th scope="col">ASSET</th>
              <th scope="col" className="num-col">MIN</th>
              <th scope="col" className="num-col">MAX</th>
              <th scope="col">
                <span className="sr-only">Clear</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {cfg.tickers.map((t) => {
              const b = cfg.bounds[t];
              const setB = (v: [number, number] | null) => {
                const nb = { ...cfg.bounds };
                if (v) nb[t] = v;
                else delete nb[t];
                set({ bounds: nb });
              };
              return (
                <tr key={t} className={b ? "active" : ""}>
                  <th scope="row" className="num">{t}</th>
                  <td>
                    <NumberField ariaLabel={`${t} minimum weight`} value={b ? b[0] : lo} percent min={-2} max={1} step={0.01} onChange={(v) => setB([v, b ? b[1] : cfg.maxWeight])} />
                  </td>
                  <td>
                    <NumberField ariaLabel={`${t} maximum weight`} value={b ? b[1] : cfg.maxWeight} percent min={0} max={3} step={0.01} onChange={(v) => setB([b ? b[0] : lo, v])} />
                  </td>
                  <td>
                    {b && (
                      <button type="button" className="op-x" aria-label={`Clear bounds for ${t}`} onClick={() => setB(null)}>
                        ×
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <div className="op-rail-foot num">
        W {fmtPct(lo, 0)} … {fmtPct(cfg.maxWeight, 0)}
        {!cfg.longOnly && cfg.maxGross ? ` · GROSS ≤ ${fmtNum(cfg.maxGross, 1)}×` : ""}
        {cfg.turnoverOn && cur ? ` · TO ≤ ${fmtPct(cfg.maxTurnover, 0)}` : ""} · Σw = 100%
      </div>
    </>
  );
}
