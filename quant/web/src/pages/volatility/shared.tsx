/**
 * Small building blocks shared by the Volatility tabs (prefix `vx-`): data hooks,
 * the expiry picker, pass/fail chips, key-value lists and the Run button.
 */
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { Formula, Icon, InfoTip, Panel, Select, type InfoProp } from "../../components";
import { isDataUnavailable } from "../../lib/api";
import { fmtDate, fmtNum } from "../../lib/format";
import type { Info } from "../../lib/glossary";
import { useApiQuery } from "../../lib/query";
import type { Chain, Density, Estimator, Expiries, Realized, SliceSummary, Surface } from "./types";

// ------------------------------------------------------------------ data hooks
const enc = encodeURIComponent;

export function useRealized(ticker: string, window: number, estimator: Estimator, years: number) {
  return useApiQuery<Realized>(`/options/realized/${enc(ticker)}`, { window, estimator, years });
}
export function useExpiries(ticker: string) {
  return useApiQuery<Expiries>(`/options/expiries/${enc(ticker)}`, undefined, { placeholderData: undefined });
}
export function useSurface(ticker: string, enabled: boolean) {
  return useApiQuery<Surface>(`/options/surface/${enc(ticker)}`, undefined, { enabled, placeholderData: undefined });
}
export function useChain(ticker: string, expiry: string | undefined, maxRelSpread: number, enabled: boolean) {
  return useApiQuery<Chain>(`/options/chain/${enc(ticker)}`, { expiry, max_rel_spread: maxRelSpread }, { enabled: enabled && !!expiry });
}
export function useDensity(ticker: string, expiry: string | undefined, enabled: boolean) {
  return useApiQuery<Density>(`/options/density/${enc(ticker)}`, { expiry }, { enabled: enabled && !!expiry });
}

/** Default expiry: the first one at least ~3 weeks out (short expiries have noisy smiles). */
export function defaultExpiry(list: SliceSummary[] | undefined): string | undefined {
  if (!list?.length) return undefined;
  return (list.find((e) => e.dte >= 21) ?? list[list.length - 1]).slice;
}

export function expiryLabel(e: { expiry: string; dte: number; settlement?: string }): string {
  return `${fmtDate(e.expiry)} · ${Math.round(e.dte)}d${e.settlement === "AM" ? " · AM" : ""}`;
}

/** Days formatter: "36 d" or "8.2 d" for the very short end. */
export const fmtDays = (d: number | null | undefined) => (d == null || !Number.isFinite(d) ? "—" : `${d < 10 ? fmtNum(d, 1) : fmtNum(d, 0)} d`);

// ------------------------------------------------------------------ controls
export function ExpirySelect({ expiries, value, onChange }: { expiries: SliceSummary[]; value: string | undefined; onChange: (v: string) => void }) {
  if (!expiries.length || !value) return null;
  return (
    <label className="vx-expiry">
      <span className="vx-expiry-label">Expiry</span>
      <Select ariaLabel="Expiry" value={value} onChange={onChange} options={expiries.map((e) => ({ value: e.slice, label: expiryLabel(e) }))} />
    </label>
  );
}

/** Draft → committed inputs for heavy POSTs; `run()` commits the draft. */
export function useCommitted<T>(draft: T, initial?: T): { committed: T; run: () => void; dirty: boolean; commit: (v: T) => void } {
  const [committed, setCommitted] = useState<T>(initial ?? draft);
  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(committed), [draft, committed]);
  const run = useCallback(() => setCommitted(draft), [draft]);
  return { committed, run, dirty, commit: setCommitted };
}

export function RunButton({ onRun, dirty, busy, label = "Run", disabled }: { onRun: () => void; dirty: boolean; busy?: boolean; label?: string; disabled?: boolean }) {
  return (
    <button type="button" className={`btn btn-sm ${dirty ? "btn-primary" : ""} vx-run`} onClick={onRun} disabled={disabled || busy || !dirty} title={dirty ? "Inputs changed — recompute" : "Results are up to date"}>
      {busy ? <span className="vx-spinner" aria-hidden /> : <Icon name={dirty ? "refresh" : "check"} size={14} />}
      {busy ? "Running…" : dirty ? label : "Up to date"}
    </button>
  );
}

// ------------------------------------------------------------------ display
/** Pass / fail / unknown chip for diagnostics. */
export function Check({ ok, label, title }: { ok: boolean | null | undefined; label: ReactNode; title?: string }) {
  const tone = ok == null ? "unknown" : ok ? "gain" : "loss";
  return (
    <span className={`vx-check vx-check-${tone}`} title={title}>
      <Icon name={ok == null ? "help" : ok ? "check" : "x"} size={12} strokeWidth={2.2} />
      {label}
    </span>
  );
}

export function KV({ rows, cols = 1 }: { rows: { label: ReactNode; value: ReactNode; info?: InfoProp; tone?: string; hint?: ReactNode }[]; cols?: 1 | 2 }) {
  return (
    <dl className={`vx-kv ${cols === 2 ? "vx-kv-2" : ""}`}>
      {rows.map((r, i) => (
        <div className="vx-kv-row" key={i}>
          <dt>
            {r.label}
            <InfoTip info={r.info} size={12} label={typeof r.label === "string" ? r.label : undefined} />
          </dt>
          <dd className={`num ${r.tone ?? ""}`}>
            {r.value}
            {r.hint && <span className="vx-kv-hint">{r.hint}</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Legend swatch used in custom legends and tables. */
export function Swatch({ color, dash, dot }: { color: string; dash?: boolean; dot?: boolean }) {
  return <span className={`vx-swatch ${dash ? "dash" : ""} ${dot ? "dot" : ""}`} style={{ ["--sw" as string]: color }} aria-hidden />;
}

/**
 * Shown in place of a tab's panels when the live option chain is unavailable (the server
 * answers 503 offline). One honest Panel with the server's reason, then a quiet catalogue
 * of what the tab computes once the source is reachable — no placeholder numbers.
 */
export function LiveOnly({ title, error, items, onRetry, alternatives }: { title: string; error: unknown; items: { title: string; text: string; info?: Info }[]; onRetry?: () => void; alternatives?: ReactNode }) {
  return (
    <div className="stack">
      <Panel title={title} subtitle="Needs a live option chain" error={error} onRetry={onRetry} />
      {isDataUnavailable(error) && (
        <div className="vx-preview">
          <div className="vx-preview-head">
            <span className="eyebrow">When the chain is reachable, this tab shows</span>
            {alternatives && <span className="vx-preview-alt small">{alternatives}</span>}
          </div>
          <ol className="vx-preview-list">
            {items.map((it, i) => (
              <li key={it.title} className="vx-preview-item">
                <span className="vx-preview-n num">{String(i + 1).padStart(2, "0")}</span>
                <div className="vx-preview-body">
                  <div className="vx-preview-title">
                    {it.title}
                    <InfoTip info={it.info} size={12} label={it.title} />
                  </div>
                  <p className="vx-preview-text">{it.text}</p>
                  {it.info?.formula && <Formula tex={it.info.formula} className="vx-preview-formula" />}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

/** Compact grid of greek readouts (smaller than StatTile so long decimals fit). */
export function GreekGrid({ cells }: { cells: { label: string; value: string; info?: InfoProp; caption?: string }[] }) {
  return (
    <div className="vx-greeks">
      {cells.map((c) => (
        <div key={c.label} className="vx-greek">
          <div className="vx-greek-label">
            {c.label}
            <InfoTip info={c.info} size={12} label={c.label} />
          </div>
          <div className="vx-greek-value num">{c.value}</div>
          {c.caption && <div className="vx-greek-cap subtle">{c.caption}</div>}
        </div>
      ))}
    </div>
  );
}

