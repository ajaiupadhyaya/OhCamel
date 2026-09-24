/**
 * Edit the active portfolio: holdings & weights, presets from named universes, import a
 * real fund's latest 13F from SEC (top-N), or paste CSV. Also the analysis settings
 * (benchmark, notional, window).
 *
 *   <PortfolioBuilder />                         edits the global PortfolioContext
 *   <PortfolioBuilder value={p} onChange={setP} />  controlled (e.g. a scratch portfolio)
 *   <PortfolioBuilder showSettings={false} />   holdings only
 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { fmtCurrency, fmtDate, fmtPct } from "../lib/format";
import { use13F, use13FFilers, useUniverses, type Holding13F } from "../lib/market";
import { consolidate, equalWeight, grossWeight, normalizeWeights, parseHoldingsCsv, totalWeight, usePortfolio, type Portfolio } from "../lib/portfolio";
import { seriesVar } from "../lib/theme";
import type { Holding } from "../lib/types";
import { Field, NumberField, SegmentedControl, Slider } from "./Controls";
import { DateRangePicker } from "./DateRangePicker";
import { Icon } from "./Icon";
import { Provenance } from "./Provenance";
import { Callout, ErrorState, Skeleton } from "./States";
import { TickerInput } from "./TickerInput";

type Source = "presets" | "13f" | "csv";

export function PortfolioBuilder({ value, onChange, showSettings = true, showSources = true }: { value?: Portfolio; onChange?: (p: Portfolio) => void; showSettings?: boolean; showSources?: boolean }) {
  const ctx = usePortfolio();
  const p = value ?? ctx.portfolio;
  const set = onChange ?? ctx.setPortfolio;
  const setHoldings = (hs: Holding[]) => set({ ...p, holdings: hs });
  const [source, setSource] = useState<Source>("presets");

  const sum = totalWeight(p.holdings);
  const gross = grossWeight(p.holdings);
  const maxAbs = Math.max(0.0001, ...p.holdings.map((h) => Math.abs(h.weight)));

  return (
    <div className="oc-builder">
      <div className="oc-builder-head">
        <input className="oc-builder-name display" value={p.name} aria-label="Portfolio name" onChange={(e) => set({ ...p, name: e.target.value })} />
        <div className="row-wrap">
          <span className="badge">{p.holdings.length} holdings</span>
          <span className={`badge ${Math.abs(sum - 1) < 1e-6 ? "" : sum > 1 ? "warn" : "accent"}`} title="Net weight Σw">
            Σ <span className="num">{fmtPct(sum, 1)}</span>
          </span>
          {Math.abs(gross - sum) > 1e-6 && (
            <span className="badge" title="Gross exposure Σ|w|">
              gross <span className="num">{fmtPct(gross, 1)}</span>
            </span>
          )}
          {sum < 1 - 1e-6 && (
            <span className="badge" title="Uninvested (earns nothing in the analytics)">
              cash <span className="num">{fmtPct(1 - sum, 1)}</span>
            </span>
          )}
        </div>
      </div>

      <div className="oc-builder-holdings">
        {p.holdings.length === 0 && <div className="subtle small" style={{ padding: "12px 0" }}>No holdings yet — add tickers below or pick a preset.</div>}
        {p.holdings.map((h, i) => (
          <div className="oc-builder-row" key={h.ticker + i}>
            <span className="oc-chip-dot" style={{ background: seriesVar(i) }} />
            <Link className="num oc-builder-ticker" to={`/ticker/${h.ticker}`}>
              {h.ticker}
            </Link>
            <div className="oc-builder-bar" aria-hidden>
              <span style={{ width: `${(Math.abs(h.weight) / maxAbs) * 100}%`, background: h.weight < 0 ? "var(--loss)" : seriesVar(i) }} />
            </div>
            <NumberField value={h.weight} percent digits={2} width={96} onChange={(w) => setHoldings(p.holdings.map((x, j) => (j === i ? { ...x, weight: w } : x)))} />
            <button className="icon-btn" aria-label={`Remove ${h.ticker}`} onClick={() => setHoldings(p.holdings.filter((_, j) => j !== i))}>
              <Icon name="x" size={14} />
            </button>
          </div>
        ))}
      </div>

      <div className="oc-builder-tools">
        <TickerInput
          placeholder="Add ticker…"
          exclude={p.holdings.map((h) => h.ticker)}
          onSelect={(t) => {
            if (p.holdings.some((h) => h.ticker === t)) return;
            const n = p.holdings.length + 1;
            // new position gets 1/N, others scale down proportionally
            setHoldings([...p.holdings.map((h) => ({ ...h, weight: h.weight * (1 - 1 / n) })), { ticker: t, weight: 1 / n }]);
          }}
        />
        <div className="row-wrap">
          <button className="btn btn-sm" onClick={() => setHoldings(equalWeight(p.holdings.map((h) => h.ticker)))} disabled={!p.holdings.length}>
            Equal weight
          </button>
          <button className="btn btn-sm" onClick={() => setHoldings(normalizeWeights(p.holdings))} disabled={!p.holdings.length || Math.abs(sum - 1) < 1e-9}>
            Normalize to 100%
          </button>
          <button className="btn btn-sm btn-ghost" onClick={() => setHoldings([])} disabled={!p.holdings.length}>
            Clear
          </button>
        </div>
      </div>

      {showSources && (
        <div className="oc-builder-sources">
          <SegmentedControl
            size="sm"
            value={source}
            onChange={setSource}
            options={[
              { value: "presets", label: "Presets" },
              { value: "13f", label: "Import 13F" },
              { value: "csv", label: "Paste CSV" },
            ]}
          />
          <div className="oc-builder-source-body">
            {source === "presets" && <Presets onPick={(name, ts) => set({ ...p, name, holdings: equalWeight(ts) })} />}
            {source === "13f" && <Import13F onUse={(name, hs) => set({ ...p, name, holdings: hs })} />}
            {source === "csv" && <CsvPaste onUse={(hs) => setHoldings(hs)} />}
          </div>
        </div>
      )}

      {showSettings && (
        <div className="oc-builder-settings">
          <Field label="Benchmark" info={{ text: "Used for beta, active risk, tracking error and attribution." }}>
            <TickerInput clearOnSelect={false} initial={p.benchmark} key={p.benchmark} placeholder="SPY" onSelect={(t) => set({ ...p, benchmark: t })} />
          </Field>
          <NumberField label="Notional" unit="$" value={p.notional} min={1} step={100000} digits={0} info={{ text: "Portfolio value in USD, used to express VaR, ES and stress losses in dollars." }} hint={fmtCurrency(p.notional, { compact: true })} onChange={(v) => set({ ...p, notional: v })} />
          <Field label="Analysis window">
            <DateRangePicker value={{ start: p.start, end: p.end }} onChange={({ start, end }) => set({ ...p, start, end })} />
          </Field>
        </div>
      )}
    </div>
  );
}

function Presets({ onPick }: { onPick: (name: string, tickers: string[]) => void }) {
  const q = useUniverses();
  if (q.isLoading) return <Skeleton height={64} />;
  if (q.isError) return <ErrorState error={q.error} compact onRetry={() => q.refetch()} />;
  const us = (q.data ?? []).filter((u) => u.tickers.length);
  if (!us.length) return <div className="subtle small">No named universes are published by the API.</div>;
  return (
    <div className="oc-presets">
      {us.map((u) => (
        <button key={u.name} className="oc-preset" onClick={() => onPick(u.label, u.tickers)} title={u.tickers.join(", ")}>
          <span className="oc-preset-name">{u.label}</span>
          <span className="oc-preset-meta num">{u.tickers.length} tickers · equal weight</span>
          {u.description && <span className="oc-preset-desc">{u.description}</span>}
        </button>
      ))}
    </div>
  );
}

function Import13F({ onUse }: { onUse: (name: string, hs: Holding[]) => void }) {
  const filers = use13FFilers();
  const [cik, setCik] = useState<string>("");
  const [loadCik, setLoadCik] = useState<string | null>(null);
  const [topN, setTopN] = useState(20);
  const filing = use13F(loadCik);

  const preview = useMemo(() => {
    const all = (filing.data?.holdings ?? []).filter((h) => !h.put_call);
    const hs = consolidateByTicker(all.filter((h) => h.ticker));
    const top = hs.slice(0, topN);
    return { top, mapped: hs.length, unmapped: all.length - all.filter((h) => h.ticker).length, covered: top.reduce((s, h) => s + h.weight, 0) };
  }, [filing.data, topN]);

  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="row-wrap">
        {filers.data && filers.data.length > 0 ? (
          <select className="select" value={cik} onChange={(e) => setCik(e.target.value)} aria-label="13F filer" style={{ minWidth: 220 }}>
            <option value="">Choose a fund…</option>
            {filers.data.map((f) => (
              <option key={f.cik} value={f.cik}>
                {f.manager ? `${f.manager} — ${f.name}` : f.name}
              </option>
            ))}
          </select>
        ) : null}
        <input className="input num" placeholder="or CIK, e.g. 1067983" value={cik} onChange={(e) => setCik(e.target.value.replace(/\D/g, ""))} style={{ width: 170 }} />
        <button className="btn btn-sm btn-primary" disabled={!cik} onClick={() => setLoadCik(cik)}>
          Load latest 13F
        </button>
      </div>
      {filers.isError && !filers.data && <div className="subtle small">Filer list unavailable ({filers.error?.detail}). You can still enter a CIK.</div>}
      {loadCik && filing.isLoading && <Skeleton height={120} />}
      {loadCik && filing.isError && <ErrorState error={filing.error} compact onRetry={() => filing.refetch()} />}
      {filing.data && (
        <>
          <div className="row-wrap small">
            <strong>{filing.data.filer}</strong>
            {filing.data.period && <span className="subtle">period {fmtDate(filing.data.period)}</span>}
            {filing.data.filed && <span className="subtle">· filed {fmtDate(filing.data.filed)}</span>}
          </div>
          <Slider label="Top N positions" value={topN} min={5} max={Math.max(5, Math.min(60, preview.mapped))} step={1} onChange={setTopN} format={(v) => `${v} · ${fmtPct(preview.covered, 1)} of fund`} />
          <div className="oc-13f-preview">
            {preview.top.map((h) => (
              <div key={h.ticker! + h.issuer} className="oc-13f-row">
                <span className="num">{h.ticker}</span>
                <span className="subtle oc-13f-issuer">{h.issuer}</span>
                <span className="num">{fmtPct(h.weight, 1)}</span>
              </div>
            ))}
          </div>
          {preview.unmapped > 0 && <Callout>{preview.unmapped} positions could not be mapped to a ticker (e.g. private placements or delisted CUSIPs) and are excluded; option rows are always excluded.</Callout>}
          <div className="row">
            <button className="btn btn-sm btn-primary" disabled={!preview.top.length} onClick={() => onUse(`${filing.data!.filer} (13F top ${preview.top.length})`, normalizeWeights(preview.top.map((h) => ({ ticker: h.ticker!, weight: h.weight }))))}>
              Use top {preview.top.length}, rescaled to 100%
            </button>
          </div>
          <Provenance items={filing.data.provenance} />
        </>
      )}
    </div>
  );
}

/** Several share classes / CUSIPs can map to one ticker: sum them. Keeps weight order. */
function consolidateByTicker(hs: Holding13F[]): Holding13F[] {
  const m = new Map<string, Holding13F>();
  for (const h of hs) {
    const cur = m.get(h.ticker!);
    if (cur) cur.weight += h.weight;
    else m.set(h.ticker!, { ...h });
  }
  return [...m.values()].sort((a, b) => b.weight - a.weight);
}

function CsvPaste({ onUse }: { onUse: (hs: Holding[]) => void }) {
  const [text, setText] = useState("");
  const parsed = useMemo(() => (text.trim() ? parseHoldingsCsv(text) : null), [text]);
  return (
    <div className="stack" style={{ gap: 10 }}>
      <textarea className="input" rows={5} placeholder={"SPY, 60%\nTLT, 30%\nGLD, 10%"} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      <div className="subtle small">One position per line: ticker, then weight as a decimal (0.6) or percent (60%). Header rows are ignored; no weights means equal weight.</div>
      {parsed?.errors.length ? <Callout tone="warn">{parsed.errors.join(" · ")}</Callout> : null}
      {parsed && parsed.holdings.length > 0 && (
        <div className="row-wrap">
          <button className="btn btn-sm btn-primary" onClick={() => onUse(consolidate(parsed.holdings))}>
            Replace holdings with {parsed.holdings.length} rows
          </button>
          <span className="subtle small num">Σ {fmtPct(totalWeight(parsed.holdings), 1)}</span>
        </div>
      )}
    </div>
  );
}
