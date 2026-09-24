/**
 * Number / date formatters. All accept null/undefined/NaN and return EM_DASH.
 *
 * Unit conventions (match the backend):
 *   - returns, weights, vols, VaR as a fraction  -> DECIMALS (0.012 = 1.2%)  -> fmtPct
 *   - yields from FRED                           -> PERCENT (4.25 = 4.25%)   -> fmtPct(x / 100) or fmtPctPoints
 *   - spreads / small changes                    -> fmtBps(decimal)          (0.0012 -> "12 bp")
 */

export const EM_DASH = "—";
const MINUS = "−"; // typographic minus

const ok = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const minus = (s: string) => s.replace(/^-/, MINUS);

const nfCache = new Map<string, Intl.NumberFormat>();
function nf(opts: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = JSON.stringify(opts);
  let f = nfCache.get(key);
  if (!f) {
    f = new Intl.NumberFormat("en-US", opts);
    nfCache.set(key, f);
  }
  return f;
}

/** 1234.5 -> "1,234.50" */
export function fmtNum(x: number | null | undefined, digits = 2, opts: { signed?: boolean } = {}): string {
  if (!ok(x)) return EM_DASH;
  const s = nf({ minimumFractionDigits: digits, maximumFractionDigits: digits }).format(x);
  return minus(opts.signed && x > 0 ? `+${s}` : s);
}

/** 0.01234 -> "1.23%" (decimal in). `signed` adds "+" to positives. */
export function fmtPct(x: number | null | undefined, digits = 2, opts: { signed?: boolean } = {}): string {
  if (!ok(x)) return EM_DASH;
  const s = nf({ minimumFractionDigits: digits, maximumFractionDigits: digits }).format(x * 100);
  return minus(`${opts.signed && x > 0 ? "+" : ""}${s}%`);
}

/** Signed percent shorthand: 0.012 -> "+1.20%". */
export const fmtSignedPct = (x: number | null | undefined, digits = 2) => fmtPct(x, digits, { signed: true });

/** Value already in percent units (FRED yields): 4.253 -> "4.25%". */
export function fmtPctPoints(x: number | null | undefined, digits = 2, opts: { signed?: boolean } = {}): string {
  return ok(x) ? fmtPct(x / 100, digits, opts) : EM_DASH;
}

/** Decimal -> basis points: 0.00123 -> "12 bp"; `signed` for changes. */
export function fmtBps(x: number | null | undefined, digits = 0, opts: { signed?: boolean } = {}): string {
  if (!ok(x)) return EM_DASH;
  const v = x * 10_000;
  const s = nf({ minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v);
  return minus(`${opts.signed && v > 0 ? "+" : ""}${s} bp`);
}

/** USD. compact: 1.2e6 -> "$1.2M". */
export function fmtCurrency(x: number | null | undefined, opts: { digits?: number; compact?: boolean; signed?: boolean; currency?: string } = {}): string {
  if (!ok(x)) return EM_DASH;
  const { compact = false, signed = false, currency = "USD" } = opts;
  const digits = opts.digits ?? (compact ? 1 : Math.abs(x) >= 1000 ? 0 : 2);
  const s = nf({
    style: "currency",
    currency,
    notation: compact ? "compact" : "standard",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(x);
  return minus(signed && x > 0 ? `+${s}` : s);
}

/** 1_234_567 -> "1.23M" */
export function fmtCompact(x: number | null | undefined, digits = 2): string {
  if (!ok(x)) return EM_DASH;
  return minus(nf({ notation: "compact", maximumFractionDigits: digits }).format(x));
}

/** Multiples, e.g. 1.234 -> "1.23×" */
export function fmtMultiple(x: number | null | undefined, digits = 2): string {
  return ok(x) ? `${fmtNum(x, digits)}×` : EM_DASH;
}

/** Heuristic formatter for tables with mixed magnitudes. */
export function fmtAuto(x: unknown): string {
  if (x === null || x === undefined) return EM_DASH;
  if (typeof x !== "number") return String(x);
  if (!Number.isFinite(x)) return EM_DASH;
  const a = Math.abs(x);
  if (a !== 0 && (a >= 1e6 || a < 1e-3)) return fmtCompact(x, 3);
  if (Number.isInteger(x)) return fmtNum(x, 0);
  return fmtNum(x, a >= 100 ? 1 : a >= 1 ? 2 : 4);
}

// ---------------------------------------------------------------- dates

/** Parse "2024-01-31" as a LOCAL date (avoids the UTC off-by-one of new Date("YYYY-MM-DD")). */
export function parseDate(s: string | number | Date | null | undefined): Date | null {
  if (s === null || s === undefined || s === "") return null;
  if (s instanceof Date) return s;
  if (typeof s === "number") return new Date(s);
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (m) return new Date(+m[1], +m[2] - 1, +m[3]);
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "2024-01-31" -> "31 Jan 2024" (style "medium"), "Jan 2024" ("month"), "2024-01-31" ("iso"). */
const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * Date styles: "medium" 11 Feb 2026 · "short" 11 Feb · "short-year" 11 Feb 26 ·
 * "month" Feb 2026 · "year" 2026 · "iso" 2026-02-11.
 */
export function fmtDate(s: string | number | Date | null | undefined, style: "medium" | "short" | "short-year" | "month" | "iso" | "year" = "medium"): string {
  const d = parseDate(s);
  if (!d) return EM_DASH;
  switch (style) {
    case "iso":
      return toIsoDate(d);
    case "month":
      return d.toLocaleDateString("en-GB", { month: "short", year: "numeric" });
    case "year":
      return String(d.getFullYear());
    case "short":
      return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
    case "short-year":
      return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]} ${String(d.getFullYear() % 100).padStart(2, "0")}`;
    default:
      return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  }
}

export function toIsoDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** "3 min ago", "2 h ago", "5 d ago", or a date for anything older than 30 days. */
export function fmtRelativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  const d = parseDate(iso ?? null);
  if (!d) return EM_DASH;
  const s = Math.round((now.getTime() - d.getTime()) / 1000);
  if (s < 0) return "just now";
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 30 * 86400) return `${Math.round(s / 86400)} d ago`;
  return fmtDate(d);
}

// ---------------------------------------------------------------- colouring

/** CSS class for a signed value: "gain" | "loss" | "" (zero / missing). */
export function signClass(x: number | null | undefined, invert = false): "gain" | "loss" | "" {
  if (!ok(x) || x === 0) return "";
  return (x > 0) !== invert ? "gain" : "loss";
}
