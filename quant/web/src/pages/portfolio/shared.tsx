/**
 * Small building blocks shared by the Portfolio Lab tabs (prefix `pl-`).
 */
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { Icon, InfoTip, type InfoProp } from "../../components";
import { fmtCurrency, fmtNum, fmtPct } from "../../lib/format";

/**
 * Draft → committed inputs for heavy POSTs. The first draft is committed immediately (so
 * results show on load); later edits only take effect when `run()` is called.
 */
export function useCommitted<T>(draft: T): { committed: T; run: () => void; dirty: boolean } {
  const [committed, setCommitted] = useState<T>(draft);
  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(committed), [draft, committed]);
  const run = useCallback(() => setCommitted(draft), [draft]);
  return { committed, run, dirty };
}

/** The "Run" button for a heavy computation; highlighted when inputs changed. */
export function RunButton({ onRun, dirty, busy, label = "Run", disabled }: { onRun: () => void; dirty: boolean; busy?: boolean; label?: string; disabled?: boolean }) {
  return (
    <button type="button" className={`btn btn-sm ${dirty ? "btn-primary" : ""} pl-run`} onClick={onRun} disabled={disabled || busy || !dirty} title={dirty ? "Inputs changed — recompute" : "Results are up to date"}>
      {busy ? <span className="pl-spinner" aria-hidden /> : <Icon name={dirty ? "refresh" : "check"} size={14} />}
      {busy ? "Running…" : dirty ? label : "Up to date"}
    </button>
  );
}

/** Label/value rows with info tips — for dense secondary statistics. */
export function KV({ rows, cols = 1 }: { rows: { label: ReactNode; value: ReactNode; info?: InfoProp; tone?: string; hint?: ReactNode }[]; cols?: 1 | 2 }) {
  return (
    <dl className={`pl-kv ${cols === 2 ? "pl-kv-2" : ""}`}>
      {rows.map((r, i) => (
        <div className="pl-kv-row" key={i}>
          <dt>
            {r.label}
            <InfoTip info={r.info} size={12} label={typeof r.label === "string" ? r.label : undefined} />
          </dt>
          <dd className={`num ${r.tone ?? ""}`}>
            {r.value}
            {r.hint && <span className="pl-kv-hint">{r.hint}</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Basel traffic-light chip. */
export function ZoneChip({ zone, title }: { zone: string | null | undefined; title?: string }) {
  if (!zone) return <span className="subtle">—</span>;
  const tone = zone === "green" ? "gain" : zone === "yellow" ? "warn" : "loss";
  return (
    <span className={`pl-zone pl-zone-${tone}`} title={title}>
      <span className="pl-zone-dot" aria-hidden />
      {zone}
    </span>
  );
}

/** A p-value cell: vermilion when the model is rejected at 5%. */
export function PValue({ p, digits = 3 }: { p: number | null | undefined; digits?: number }) {
  if (p === null || p === undefined || !Number.isFinite(p)) return <span className="subtle">—</span>;
  const rejected = p < 0.05;
  return (
    <span className={rejected ? "loss" : ""} title={rejected ? "Rejected at the 5% level" : "Not rejected at 5%"}>
      {p < 0.001 ? "<0.001" : fmtNum(p, digits)}
      {rejected && <span className="pl-reject" aria-label="rejected">✕</span>}
    </span>
  );
}

/** Percent with dollars underneath. */
export function PctUsd({ pct, usd, digits = 2 }: { pct: number | null | undefined; usd: number | null | undefined; digits?: number }) {
  return (
    <span className="pl-pctusd">
      <span>{fmtPct(pct, digits)}</span>
      <span className="pl-usd">{fmtCurrency(usd, { compact: Math.abs(usd ?? 0) >= 100_000, digits: Math.abs(usd ?? 0) >= 100_000 ? 1 : 0 })}</span>
    </span>
  );
}

/** Plain-English "reading" line shown above a chart or table. */
export function Reading({ children }: { children: ReactNode }) {
  return (
    <p className="pl-reading">
      <span className="pl-reading-mark" aria-hidden>
        ¶
      </span>
      <span>{children}</span>
    </p>
  );
}

export const usd = (v: number | null | undefined, signed = false) => fmtCurrency(v, { compact: Math.abs(v ?? 0) >= 10_000, digits: Math.abs(v ?? 0) >= 10_000 ? 1 : 0, signed });

/** Series/frame dates are ISO strings; this keeps charts typed. */
export const asDates = (xs: (string | number)[]) => xs.map(String);
