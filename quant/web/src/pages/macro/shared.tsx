/**
 * Rates & Macro — page-local building blocks (prefix `mc-`): value formatting by FRED units,
 * ruled label / value rows, the readout line, the control strip, the percentile tick and the
 * live-only block a tab shows when its FRED series are unavailable. Paper Tape: rules, caps
 * labels, mono numbers, signal only for a loss or a breach.
 */
import type { CSSProperties, ReactNode } from "react";
import { InfoTip, type InfoProp } from "../../components";
import { Absent, Note } from "../../design";
import { ApiError, isDataUnavailable } from "../../lib/api";
import { EM_DASH, fmtCompact, fmtCurrency, fmtNum, fmtPct, parseDate, toIsoDate } from "../../lib/format";

export { STD_TENORS, tenorLabel, tenorTicks } from "./derive";

// ------------------------------------------------------------------ values
/** Format a FRED value by its units string ("%", "pp", "% y/y", "thousands", "index", "USD millions", …). */
export function fmtUnits(v: number | null | undefined, units: string, opts: { signed?: boolean; change?: boolean } = {}): string {
  if (v == null || !Number.isFinite(v)) return EM_DASH;
  const u = units.toLowerCase();
  const signed = !!opts.signed;
  if (u.startsWith("%")) return opts.change ? `${fmtNum(v, 2, { signed })} PP` : fmtPct(v / 100, 2, { signed });
  if (u === "pp") return `${fmtNum(v, 2, { signed })} PP`;
  if (u === "thousands") return `${fmtNum(v, 0, { signed })}K`;
  if (u === "claims") return (signed && v > 0 ? "+" : "") + fmtCompact(v, 1);
  if (u.startsWith("usd millions")) return fmtCurrency(v * 1e6, { compact: true, signed });
  if (u.startsWith("usd")) return fmtCurrency(v, { digits: 2, signed });
  return fmtNum(v, Math.abs(v) >= 100 ? 1 : 2, { signed });
}

/** Ordinal percentile label: 0.87 -> "87TH". */
export function ordinal(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return EM_DASH;
  const n = Math.round(p * 100);
  const s = n % 100 >= 11 && n % 100 <= 13 ? "TH" : ({ 1: "ST", 2: "ND", 3: "RD" } as Record<number, string>)[n % 10] ?? "TH";
  return `${n}${s}`;
}

/** Basis points from a percent-point difference: 0.42 -> "+42 BP". */
export const fmtBpFromPp = (pp: number | null | undefined, signed = true) => (pp == null || !Number.isFinite(pp) ? EM_DASH : `${fmtNum(pp * 100, 0, { signed })} BP`);

/** Earliest ISO date for a "last N years" window ending at `end` (or today). */
export function yearsBack(n: number | null, end?: string): string | undefined {
  if (n == null) return undefined;
  const d = end ? parseDate(end)! : new Date();
  return toIsoDate(new Date(d.getFullYear() - n, d.getMonth(), d.getDate()));
}

// ------------------------------------------------------------------ display
/** Where a value sits in its own history: a hairline track with a tick at the percentile. */
export function PctTick({ value, label }: { value: number | null | undefined; label?: string }) {
  if (value == null || !Number.isFinite(value)) return <span className="mc-pct num">{EM_DASH}</span>;
  const at = Math.min(1, Math.max(0, value)) * 100;
  return (
    <span className="mc-pct num" title={label}>
      <span className="mc-pct-bar" style={{ "--at": `${at}%` } as CSSProperties} aria-hidden>
        <span />
      </span>
      {ordinal(value)}
    </span>
  );
}

/** Ruled label / value rows; InfoTips only on metric labels. */
export function KV({ rows, cols = 1 }: { rows: { k: ReactNode; v: ReactNode; info?: InfoProp; tone?: string }[]; cols?: 1 | 2 }) {
  return (
    <dl className={`mc-kv ${cols === 2 ? "mc-kv-2" : ""}`}>
      {rows.map((r, i) => (
        <div key={i} className="mc-kv-row">
          <dt>
            {r.k}
            <InfoTip info={r.info} size={12} label={typeof r.k === "string" ? r.k : undefined} />
          </dt>
          <dd className={`num ${r.tone ?? ""}`}>{r.v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A terse readout line: KEY value · KEY value. */
export function Readline({ items }: { items: ({ k: string; v: ReactNode; tone?: string } | false | null | undefined)[] }) {
  return (
    <div className="mc-readline num">
      {items
        .filter((it): it is { k: string; v: ReactNode; tone?: string } => !!it)
        .map((it, i) => (
          <span key={i}>
            <span className="mc-readline-k">{it.k}</span> <span className={it.tone ?? ""}>{it.v}</span>
          </span>
        ))}
    </div>
  );
}

/** A caps sub-heading inside a Cell. */
export function Sub({ children }: { children: ReactNode }) {
  return <h4 className="mc-sub">{children}</h4>;
}

/** The control strip above a tab's cells: caps field labels, controls inline. */
export function Controls({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mc-controls">
      <div className="mc-controls-main">{children}</div>
      {right && <div className="mc-controls-right num">{right}</div>}
    </div>
  );
}

export function Ctl({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="mc-ctl">
      <span className="mc-ctl-k">{label}</span>
      {children}
    </div>
  );
}

/**
 * In place of a tab whose FRED series are unavailable (503 offline): INSUFFICIENT DATA with the
 * server's reason, then a ruled list of what the tab computes, each with its Methodology note.
 */
export function LiveOnly({ title, error, items, source, actions }: { title: string; error: unknown; items: { k: string; note: string }[]; source: string; actions?: ReactNode }) {
  const reason = isDataUnavailable(error) ? "DATA UNAVAILABLE" : "SOURCE UNAVAILABLE";
  const detail = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : null;
  return (
    <section className="oc-panel mc-live-only">
      <header className="oc-panel-head">
        <h3 className="oc-panel-title">{title}</h3>
        {actions && <div className="oc-panel-meta">{actions}</div>}
      </header>
      <div className="oc-panel-body">
        <Absent reason={reason} source={detail ? `${source} · ${detail}` : source} />
        <ol className="mc-live-list num">
          {items.map((it, i) => (
            <li key={it.k}>
              <span className="mc-live-n">{String(i + 1).padStart(2, "0")}</span>
              <span className="mc-live-k">
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
