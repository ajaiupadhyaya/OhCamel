/**
 * Company page — data hooks, formatters and page-local building blocks (prefix `co-`): ruled
 * label / value rows, the zone track (a hairline scale with threshold ticks and the value
 * marked), the signed contribution bar, the caps control strip and the live-only block shown
 * when SEC EDGAR cannot be read. Paper Tape: rules, caps labels, mono numbers, signal only for
 * a loss or a breach.
 */
import type { CSSProperties, ReactNode } from "react";
import { InfoTip, type InfoProp } from "../../components";
import { Absent, Note } from "../../design";
import { ApiError, isDataUnavailable } from "../../lib/api";
import { EM_DASH, fmtCurrency, fmtNum, fmtPct } from "../../lib/format";
import { useApiPost, useApiQuery } from "../../lib/query";
import type { DcfBody, DcfOut, Period, Profile, Ratios, Scores, Statements } from "./types";

const base = (t: string) => `/fundamentals/${encodeURIComponent(t)}`;

/** Keep the previous payload only while it belongs to the same company. */
function sameTicker<T extends { ticker?: string | null }>(t: string) {
  return (prev: T | undefined) => (prev && (prev.ticker ?? "").toUpperCase() === t ? prev : undefined);
}

export const useProfile = (t: string) => useApiQuery<Profile>(`${base(t)}/profile`, undefined, { placeholderData: sameTicker<Profile>(t) });
export const useStatements = (t: string, period: Period, limit: number) =>
  useApiQuery<Statements>(`${base(t)}/statements`, { period, limit }, { placeholderData: sameTicker<Statements>(t) });
export const useRatios = (t: string) => useApiQuery<Ratios>(`${base(t)}/ratios`, undefined, { placeholderData: sameTicker<Ratios>(t) });
export const useScores = (t: string) => useApiQuery<Scores>(`${base(t)}/scores`, undefined, { placeholderData: sameTicker<Scores>(t) });
export const useDcf = (t: string, body: DcfBody) => useApiPost<DcfOut>(`${base(t)}/dcf`, body, { placeholderData: sameTicker<DcfOut>(t) });

// ------------------------------------------------------------------ formatting

/** "$146.5B" style money for headlines. */
export const money = (x: number | null | undefined, digits = 1) => fmtCurrency(x, { compact: true, digits });

/** Multiples: 23.4× */
export const mult = (x: number | null | undefined, digits = 1) => (x == null || !Number.isFinite(x) ? EM_DASH : `${fmtNum(x, digits)}×`);

/** Percent with one decimal. */
export const pct1 = (x: number | null | undefined) => fmtPct(x, 1);

const MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

/** Caps label for a fiscal period end, per statement frequency: FY25, SEP 25. */
export function periodLabel(iso: string, period: Period): string {
  const m = /^(\d{4})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const yy = m[1].slice(2);
  return period === "annual" ? `FY${yy}` : `${MON[Number(m[2]) - 1]} ${yy}`;
}

/** Last finite value of an array. */
export function lastFinite(xs: (number | null | undefined)[]): number | null {
  for (let i = xs.length - 1; i >= 0; i--) {
    const v = xs[i];
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  return null;
}

/** Where `v` sits on [min, max] as a percent, clamped (for a CSS custom property). */
export function trackPos(v: number, min: number, max: number): number {
  if (!(max > min)) return 50;
  return ((Math.min(max, Math.max(min, v)) - min) / (max - min)) * 100;
}

// ------------------------------------------------------------------ display

/** Ruled label / value rows; InfoTips only on metric labels. */
export function KV({ rows }: { rows: { k: ReactNode; v: ReactNode; info?: InfoProp; tone?: string }[] }) {
  return (
    <dl className="co-kv">
      {rows.map((r, i) => (
        <div key={i} className="co-kv-row">
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

/** A caps sub-heading inside a Cell. */
export function Sub({ children }: { children: ReactNode }) {
  return <h4 className="co-sub">{children}</h4>;
}

/** The control strip above a tab's cells. */
export function Controls({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="co-controls">
      <div className="co-controls-main">{children}</div>
      {right && <div className="co-controls-right num">{right}</div>}
    </div>
  );
}

export function Ctl({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="co-ctl">
      <span className="co-ctl-k">{label}</span>
      {children}
    </div>
  );
}

export interface Zone {
  from: number;
  to: number;
  label: string;
  /** The breach side of a threshold (distress, manipulator-like): its label in signal. */
  breach?: boolean;
}

/**
 * A hairline scale [min, max] with threshold ticks, zone labels and the value marked by a tick.
 * The value is clamped for drawing; the true value is printed with ◂ / ▸ when off-scale.
 */
export function ZoneTrack({ min, max, zones = [], value, format, ticks = [] }: { min: number; max: number; zones?: Zone[]; value: number | null; format: (v: number) => string; ticks?: number[] }) {
  const off = value != null && (value < min || value > max);
  const pos = (v: number) => `${trackPos(v, min, max)}%`;
  return (
    <div className="co-track">
      <div className="co-track-zones">
        {zones.map((z) => (
          <span key={z.label} className={`co-track-zone ${z.breach ? "loss" : ""}`} style={{ "--at": pos(z.from), "--w": `${trackPos(z.to, min, max) - trackPos(z.from, min, max)}%` } as CSSProperties}>
            {z.label}
          </span>
        ))}
      </div>
      <div className="co-track-line">
        {ticks.map((t) => (
          <span key={t} className="co-track-tick" style={{ "--at": pos(t) } as CSSProperties} />
        ))}
        {value != null && Number.isFinite(value) && <span className="co-track-mark" style={{ "--at": pos(value) } as CSSProperties} />}
      </div>
      <div className="co-track-scale num">
        <span>{format(min)}</span>
        {ticks.map((t) => (
          <span key={t} className="co-track-tlabel" style={{ "--at": pos(t) } as CSSProperties}>
            {format(t)}
          </span>
        ))}
        <span>{format(max)}</span>
      </div>
      {value != null && Number.isFinite(value) && off && <div className="co-track-off num">{value < min ? `◂ ${format(value)} BELOW SCALE` : `${format(value)} ABOVE SCALE ▸`}</div>}
    </div>
  );
}

/** A signed proportional bar centred at zero (contributions): positive solid, negative hollow. */
export function ContributionBar({ value, max }: { value: number | null | undefined; max: number }) {
  if (value == null || !Number.isFinite(value) || max <= 0) return <span className="co-cbar" aria-hidden />;
  const w = Math.min(1, Math.abs(value) / max) * 50;
  return (
    <span className="co-cbar" aria-hidden>
      <span className={`co-cbar-fill ${value < 0 ? "neg" : "pos"}`} style={{ "--w": `${w}%` } as CSSProperties} />
    </span>
  );
}

/**
 * In place of the fundamentals when SEC EDGAR cannot be read (offline build, or not an SEC
 * filer): INSUFFICIENT DATA with the server's reason, then a ruled list of what the page
 * computes, each with its Methodology note.
 */
export function LiveOnly({ title, error, items, source }: { title: ReactNode; error: unknown; items: { k: string; note: string }[]; source: string }) {
  const reason = isDataUnavailable(error) ? "DATA UNAVAILABLE" : "SOURCE UNAVAILABLE";
  const detail = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : null;
  return (
    <section className="oc-panel co-live-only">
      <header className="oc-panel-head">
        <h3 className="oc-panel-title">{title}</h3>
      </header>
      <div className="oc-panel-body">
        <Absent reason={reason} source={detail ? `${source} · ${detail}` : source} />
        <ol className="co-live-list num">
          {items.map((it, i) => (
            <li key={it.k}>
              <span className="co-live-n">{String(i + 1).padStart(2, "0")}</span>
              <span className="co-live-k">
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
