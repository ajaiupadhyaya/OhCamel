/**
 * Building blocks shared by the Volatility views (prefix `vx-`): data hooks, the expiry
 * picker, PASS / FAIL words, ruled label / value rows, the RUN button and the live-only
 * absence block. Paper Tape: rules, caps labels, mono numbers, signal only for a failure.
 */
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { InfoTip, Select, type InfoProp } from "../../components";
import { Absent, Note } from "../../design";
import { ApiError, isDataUnavailable } from "../../lib/api";
import { fmtDate, fmtNum } from "../../lib/format";
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
  return `${fmtDate(e.expiry).toUpperCase()} · ${Math.round(e.dte)}D${e.settlement === "AM" ? " · AM" : ""}`;
}

/** Days: "36D", or "8.2D" at the very short end. */
export const fmtDays = (d: number | null | undefined) => (d == null || !Number.isFinite(d) ? "—" : `${d < 10 ? fmtNum(d, 1) : fmtNum(d, 0)}D`);

/** Vol points from a decimal difference: +0.065 → "+6.5". */
export const fmtVolPts = (v: number | null | undefined, digits = 1) => (v == null || !Number.isFinite(v) ? "—" : fmtNum(v * 100, digits, { signed: true }));

// ------------------------------------------------------------------ controls
export function ExpirySelect({ expiries, value, onChange }: { expiries: SliceSummary[]; value: string | undefined; onChange: (v: string) => void }) {
  if (!expiries.length || !value) return null;
  return (
    <label className="vx-expiry">
      <span className="vx-expiry-label">EXPIRY</span>
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

/** RUN for a heavy computation: armed (ink ground) when inputs changed, CURRENT otherwise. */
export function RunButton({ onRun, dirty, busy, label = "RUN", disabled }: { onRun: () => void; dirty: boolean; busy?: boolean; label?: string; disabled?: boolean }) {
  return (
    <button type="button" className={`btn btn-sm vx-run ${dirty ? "btn-primary" : ""}`} onClick={onRun} disabled={disabled || busy || !dirty}>
      {busy ? "RUNNING" : dirty ? label : "CURRENT"}
    </button>
  );
}

// ------------------------------------------------------------------ display
/** A diagnostic as a caps word: PASS in ink, FAIL in signal, — when unknown. */
export function Check({ ok, label }: { ok: boolean | null | undefined; label?: ReactNode }) {
  const word = ok == null ? "—" : ok ? "PASS" : "FAIL";
  return (
    <span className="vx-check num">
      {label && <span className="vx-check-k">{label}</span>}
      <span className={ok === false ? "loss" : ""}>{word}</span>
    </span>
  );
}

/** Ruled label / value rows; InfoTips only on metric labels. */
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

/** A terse ruled readout line: KEY value · KEY value. */
export function Readline({ items }: { items: { k: string; v: ReactNode; tone?: string }[] }) {
  return (
    <div className="vx-readline num">
      {items.map((it, i) => (
        <span key={i} className="vx-readline-item">
          <span className="vx-readline-k">{it.k}</span> <span className={it.tone ?? ""}>{it.v}</span>
        </span>
      ))}
    </div>
  );
}

/** Greek readouts: caps label, mono value, unit line. */
export function GreekGrid({ cells }: { cells: { label: string; value: string; info?: InfoProp; caption?: string; sym?: boolean }[] }) {
  return (
    <div className="vx-greeks">
      {cells.map((c) => (
        <div key={c.label} className="vx-greek">
          <div className="vx-greek-label">
            {c.sym ? <span className="vx-sym">{c.label}</span> : c.label}
            <InfoTip info={c.info} size={12} label={c.label} />
          </div>
          <div className="vx-greek-value num">{c.value}</div>
          {c.caption && <div className="vx-greek-cap num">{c.caption}</div>}
        </div>
      ))}
    </div>
  );
}

/**
 * In place of a live-chain view when the chain is unavailable (503 offline): INSUFFICIENT
 * DATA with the server's reason, then a ruled list of what the view computes once Cboe is
 * reachable, each with its Methodology note. No placeholder numbers.
 */
export function LiveOnly({ title, error, items, source, actions }: { title: string; error: unknown; items: { k: string; note: string }[]; source: string; actions?: ReactNode }) {
  const reason = isDataUnavailable(error) ? "DATA UNAVAILABLE" : error instanceof ApiError && error.status === 404 ? "NO OPTIONS LISTED" : "CHAIN UNAVAILABLE";
  const detail = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : null;
  return (
    <section className="oc-panel vx-live-only">
      <header className="oc-panel-head">
        <h3 className="oc-panel-title">{title}</h3>
        {actions && <div className="oc-panel-meta">{actions}</div>}
      </header>
      <div className="oc-panel-body">
        <Absent reason={reason} source={detail ? `${source} · ${detail}` : source} />
        <ol className="vx-live-list num">
          {items.map((it, i) => (
            <li key={it.k}>
              <span className="vx-live-n">{String(i + 1).padStart(2, "0")}</span>
              <span className="vx-live-k">
                {it.k}
                <Note n={i + 1} to={it.note} />
              </span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
