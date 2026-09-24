/** Company page — data hooks, formatters and small page-local building blocks. */
import type { ReactNode } from "react";
import { fmtCurrency, fmtNum, fmtPct } from "../../lib/format";
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
export const mult = (x: number | null | undefined, digits = 1) => (x == null || !Number.isFinite(x) ? "—" : `${fmtNum(x, digits)}×`);

/** Percent with one decimal. */
export const pct1 = (x: number | null | undefined) => fmtPct(x, 1);

/** Human label for a fiscal period end, per statement frequency. */
export function periodLabel(iso: string, period: Period): string {
  const d = new Date(iso + "T00:00:00");
  if (Number.isNaN(d.getTime())) return iso;
  const mon = d.toLocaleString("en-US", { month: "short" });
  if (period === "annual") return `FY${String(d.getFullYear()).slice(2)}`;
  return `${mon} ’${String(d.getFullYear()).slice(2)}`;
}

/** Last finite value of an array. */
export function lastFinite(xs: (number | null | undefined)[]): number | null {
  for (let i = xs.length - 1; i >= 0; i--) {
    const v = xs[i];
    if (typeof v === "number" && Number.isFinite(v)) return v;
  }
  return null;
}

// ------------------------------------------------------------------ zone gauge

export interface Zone {
  from: number;
  to: number;
  tone: "gain" | "loss" | "warn" | "neutral";
  label: string;
}

/**
 * Horizontal zone bar with a marker for the value (used for Altman Z, Beneish M, accruals).
 * The value is clamped into [min, max] for drawing; the true value is printed.
 */
export function ZoneGauge({ min, max, zones, value, format, markers = [], caption }: { min: number; max: number; zones: Zone[]; value: number | null; format: (v: number) => string; markers?: { at: number; label: string }[]; caption?: ReactNode }) {
  const pos = (v: number) => `${((Math.min(max, Math.max(min, v)) - min) / (max - min)) * 100}%`;
  const off = value != null && (value < min || value > max);
  return (
    <div className="co-gauge">
      <div className="co-gauge-track">
        {zones.map((z) => (
          <div key={z.label} className={`co-gauge-zone co-tone-${z.tone}`} style={{ left: pos(z.from), width: `calc(${pos(z.to)} - ${pos(z.from)})` }}>
            <span>{z.label}</span>
          </div>
        ))}
        {markers.map((m) => (
          <div key={m.label} className="co-gauge-tick" style={{ left: pos(m.at) }} title={m.label}>
            <span className="num">{m.label}</span>
          </div>
        ))}
        {value != null && Number.isFinite(value) && (
          <div className="co-gauge-marker" style={{ left: pos(value) }}>
            <span className="num">
              {off ? (value < min ? "◂ " : "") : ""}
              {format(value)}
              {off && value > max ? " ▸" : ""}
            </span>
          </div>
        )}
      </div>
      <div className="co-gauge-scale num">
        <span>{format(min)}</span>
        <span>{format(max)}</span>
      </div>
      {caption && <div className="co-gauge-caption subtle small">{caption}</div>}
    </div>
  );
}

/** A small proportional bar for contributions (signed, centred at zero). */
export function ContributionBar({ value, max }: { value: number | null | undefined; max: number }) {
  if (value == null || !Number.isFinite(value) || max <= 0) return <span className="co-cbar" />;
  const w = Math.min(1, Math.abs(value) / max) * 50;
  return (
    <span className="co-cbar" aria-hidden>
      <span className={`co-cbar-fill ${value < 0 ? "neg" : "pos"}`} style={value < 0 ? { right: "50%", width: `${w}%` } : { left: "50%", width: `${w}%` }} />
    </span>
  );
}
