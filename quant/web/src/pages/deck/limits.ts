/**
 * The viewer's limits — policy, not market data — as sent to POST /deck/reading.
 *
 * Wire shape (LimitIn): `{name, kind, threshold, ticker?}`. Every threshold is a DECIMAL:
 * fraction kinds (gross, net, name, drawdown) are fractions of equity; money kinds (var, es,
 * day_loss) are fractions of NOTIONAL, which the server multiplies out and evaluates in
 * dollars. `ticker` only applies to kind "name" (absent = the largest holding).
 *
 * Stored in localStorage["ohcamel.deck.limits.v1"] only once the viewer edits them; until
 * then the page sends `limits: null` and the server applies its own defaults (which
 * GET /deck/books also reports, and which DEFAULT_LIMITS mirrors as the fallback).
 */

export type LimitKind = "gross" | "net" | "name" | "var" | "es" | "drawdown" | "day_loss";

export interface LimitIn {
  name: string;
  kind: LimitKind;
  threshold: number;
  ticker?: string | null;
}

export const STORAGE_KEY = "ohcamel.deck.limits.v1";

export const KINDS: { kind: LimitKind; label: string; money: boolean; observed: string }[] = [
  { kind: "gross", label: "Gross exposure", money: false, observed: "Σ|w′| — gross exposure at live weights" },
  { kind: "net", label: "Net exposure", money: false, observed: "|Σw′| — net exposure at live weights" },
  { kind: "name", label: "Single name", money: false, observed: "max |w′ᵢ|, or one ticker's |w′|" },
  { kind: "var", label: "Value at risk", money: true, observed: "1-day parametric VaR at live weights, in dollars" },
  { kind: "es", label: "Expected shortfall", money: true, observed: "1-day parametric ES at live weights, in dollars" },
  { kind: "drawdown", label: "Drawdown", money: false, observed: "current drawdown, today's move included" },
  { kind: "day_loss", label: "Day loss", money: true, observed: "max(0, −day P&L), in dollars" },
];
const KIND_SET = new Set<string>(KINDS.map((k) => k.kind));
export const isMoneyKind = (k: LimitKind) => KINDS.find((x) => x.kind === k)?.money ?? false;

/** The spec's defaults (docs/superpowers/specs/2026-09-24-quant-flight-deck-design.md). */
export const DEFAULT_LIMITS: LimitIn[] = [
  { name: "gross-cap", kind: "gross", threshold: 1.5 },
  { name: "net-cap", kind: "net", threshold: 1.1 },
  { name: "name-cap", kind: "name", threshold: 0.45 },
  { name: "var-cap", kind: "var", threshold: 0.02 },
  { name: "es-cap", kind: "es", threshold: 0.03 },
  { name: "dd-cap", kind: "drawdown", threshold: 0.15 },
  { name: "day-loss", kind: "day_loss", threshold: 0.015 },
];

/** A list of limits from untrusted JSON (storage, the server), or null if it is not one. */
export function sanitizeLimits(x: unknown): LimitIn[] | null {
  if (!Array.isArray(x)) return null;
  const out: LimitIn[] = [];
  const seen = new Set<string>();
  for (const raw of x) {
    if (!raw || typeof raw !== "object") continue;
    const r = raw as Record<string, unknown>;
    const name = typeof r.name === "string" ? r.name.trim() : "";
    const kind = typeof r.kind === "string" ? r.kind : "";
    const threshold = Number(r.threshold);
    if (!name || seen.has(name) || !KIND_SET.has(kind) || !Number.isFinite(threshold) || threshold < 0) continue;
    seen.add(name);
    const ticker = kind === "name" && typeof r.ticker === "string" && r.ticker.trim() ? r.ticker.trim().toUpperCase() : null;
    out.push({ name, kind: kind as LimitKind, threshold, ...(ticker ? { ticker } : {}) });
  }
  return out;
}

/** The request's `limits`: null (server defaults) until customised; tickers only on "name". */
export function toWire(custom: LimitIn[] | null): LimitIn[] | null {
  if (!custom) return null;
  return custom.map((l) => (l.kind === "name" && l.ticker ? { name: l.name, kind: l.kind, threshold: l.threshold, ticker: l.ticker } : { name: l.name, kind: l.kind, threshold: l.threshold }));
}

/** `base`, `base-2`, `base-3` … — the first name not already used. */
export function uniqueName(base: string, taken: string[]): string {
  if (!taken.includes(base)) return base;
  for (let i = 2; ; i++) if (!taken.includes(`${base}-${i}`)) return `${base}-${i}`;
}

export function loadLimits(storage: Pick<Storage, "getItem"> | undefined = globalThis.localStorage): LimitIn[] | null {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    return raw ? sanitizeLimits(JSON.parse(raw)) : null;
  } catch {
    return null;
  }
}

export function saveLimits(ls: LimitIn[] | null, storage: Pick<Storage, "setItem" | "removeItem"> | undefined = globalThis.localStorage): void {
  try {
    if (ls) storage?.setItem(STORAGE_KEY, JSON.stringify(ls));
    else storage?.removeItem(STORAGE_KEY);
  } catch {
    /* private mode / quota: the edit still applies for this visit */
  }
}
