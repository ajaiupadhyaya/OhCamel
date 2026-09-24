/**
 * The active portfolio — one per browser, shared by every page.
 *
 *   const { portfolio, request, setHoldings, update, shareUrl } = usePortfolio();
 *   const q = useApiPost<RiskOut>("/risk/var", request);   // `request` is a ready PortfolioIn body
 *
 * - Weights are signed DECIMAL fractions of equity (0.25 = 25%); they need not sum to 1
 *   (cash = 1 − Σw), matching api/models.py PortfolioIn.
 * - Persisted to localStorage ("ohcamel.portfolio.v1").
 * - Shareable: `shareUrl()` returns the current URL with ?p=<base64url JSON>. Opening such a
 *   link adopts that portfolio (and strips the parameter so a reload doesn't clobber edits).
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Holding, PortfolioIn } from "./types";

export interface Portfolio {
  name: string;
  holdings: Holding[];
  start: string | null; // ISO date or null = full history
  end: string | null;
  benchmark: string;
  notional: number; // USD
}

export const DEFAULT_PORTFOLIO: Portfolio = {
  name: "Core multi-asset",
  holdings: [
    { ticker: "SPY", weight: 0.4 },
    { ticker: "QQQ", weight: 0.15 },
    { ticker: "IWM", weight: 0.05 },
    { ticker: "TLT", weight: 0.15 },
    { ticker: "IEF", weight: 0.15 },
    { ticker: "GLD", weight: 0.1 },
  ],
  start: null,
  end: null,
  benchmark: "SPY",
  notional: 1_000_000,
};

const KEY = "ohcamel.portfolio.v1";

// ------------------------------------------------------------------ pure helpers

export const cleanTicker = (t: string) => t.trim().toUpperCase().replace(/[^A-Z0-9.\-^=]/g, "");

/** Σ weights. */
export const totalWeight = (hs: Holding[]) => hs.reduce((s, h) => s + (Number.isFinite(h.weight) ? h.weight : 0), 0);

/** Gross exposure Σ|w|. */
export const grossWeight = (hs: Holding[]) => hs.reduce((s, h) => s + Math.abs(h.weight || 0), 0);

/** Scale weights so they sum to `target` (default 1). No-op if the sum is 0. */
export function normalizeWeights(hs: Holding[], target = 1): Holding[] {
  const s = totalWeight(hs);
  if (!s) return hs;
  return hs.map((h) => ({ ...h, weight: (h.weight / s) * target }));
}

/** 1/N across tickers. */
export function equalWeight(tickers: string[]): Holding[] {
  const ts = dedupeTickers(tickers);
  return ts.map((t) => ({ ticker: t, weight: ts.length ? 1 / ts.length : 0 }));
}

export function dedupeTickers(ts: string[]): string[] {
  return [...new Set(ts.map(cleanTicker).filter(Boolean))];
}

/** Merge duplicate tickers by summing weights; drop empty tickers. */
export function consolidate(hs: Holding[]): Holding[] {
  const m = new Map<string, number>();
  for (const h of hs) {
    const t = cleanTicker(h.ticker);
    if (!t) continue;
    m.set(t, (m.get(t) ?? 0) + (Number.isFinite(h.weight) ? h.weight : 0));
  }
  return [...m].map(([ticker, weight]) => ({ ticker, weight }));
}

/**
 * Parse pasted CSV/TSV lines "TICKER,weight". Weight may be a decimal (0.25), a percent
 * ("25%") or a bare number > 1 treated as percent when the column sums to ~100.
 * A header row and blank/# lines are skipped. Missing weights -> equal weight.
 */
export function parseHoldingsCsv(text: string): { holdings: Holding[]; errors: string[] } {
  const errors: string[] = [];
  const rows: { ticker: string; raw: string | undefined; pct: boolean }[] = [];
  text.split(/\r?\n/).forEach((line, i) => {
    const l = line.trim();
    if (!l || l.startsWith("#")) return;
    const parts = l.split(/[,\t;]|\s{2,}|\s(?=[-+]?\d)/).map((p) => p.trim()).filter(Boolean);
    const t = cleanTicker(parts[0] ?? "");
    if (!t || (i === 0 && /^(TICKER|SYMBOL|NAME)$/i.test(parts[0] ?? ""))) return;
    const raw = parts[1];
    rows.push({ ticker: t, raw, pct: !!raw && raw.includes("%") });
  });
  if (!rows.length) return { holdings: [], errors: ["No rows found. Paste lines like “SPY, 0.6”."] };
  if (rows.every((r) => r.raw === undefined)) return { holdings: equalWeight(rows.map((r) => r.ticker)), errors };
  const nums = rows.map((r) => {
    const v = r.raw === undefined ? NaN : parseFloat(r.raw.replace(/[%,$\s]/g, ""));
    if (!Number.isFinite(v)) errors.push(`${r.ticker}: could not read weight “${r.raw ?? ""}”`);
    return v;
  });
  const sum = nums.reduce((s, v) => s + (Number.isFinite(v) ? v : 0), 0);
  const asPercent = rows.some((r) => r.pct) || Math.abs(sum) > 1.5;
  const holdings = rows
    .map((r, i) => ({ ticker: r.ticker, weight: Number.isFinite(nums[i]) ? (asPercent ? nums[i] / 100 : nums[i]) : 0 }))
    .filter((h) => h.weight !== 0 || !errors.length);
  return { holdings: consolidate(holdings), errors };
}

// base64url(JSON) with full unicode support
export function encodePortfolio(p: Portfolio): string {
  const json = JSON.stringify(p);
  const bytes = new TextEncoder().encode(json);
  let bin = "";
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function decodePortfolio(s: string): Portfolio | null {
  try {
    const b64 = s.replace(/-/g, "+").replace(/_/g, "/");
    const bin = atob(b64 + "===".slice((b64.length + 3) % 4));
    const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
    return sanitize(JSON.parse(new TextDecoder().decode(bytes)));
  } catch {
    return null;
  }
}

function sanitize(x: any): Portfolio | null {
  if (!x || typeof x !== "object" || !Array.isArray(x.holdings)) return null;
  const holdings = consolidate(
    x.holdings
      .filter((h: any) => h && typeof h.ticker === "string")
      .map((h: any) => ({ ticker: String(h.ticker), weight: Number(h.weight) || 0 })),
  ).slice(0, 100);
  return {
    name: typeof x.name === "string" && x.name.trim() ? x.name.slice(0, 80) : "Shared portfolio",
    holdings,
    start: typeof x.start === "string" ? x.start : null,
    end: typeof x.end === "string" ? x.end : null,
    benchmark: typeof x.benchmark === "string" && x.benchmark ? cleanTicker(x.benchmark) : "SPY",
    notional: Number(x.notional) > 0 ? Number(x.notional) : 1_000_000,
  };
}

/** The PortfolioIn request body for POST endpoints. */
export function toRequest(p: Portfolio): PortfolioIn {
  return {
    holdings: consolidate(p.holdings).filter((h) => h.weight !== 0),
    start: p.start,
    end: p.end,
    benchmark: p.benchmark,
    notional: p.notional,
  };
}

// ------------------------------------------------------------------ context

interface PortfolioCtx {
  portfolio: Portfolio;
  /** Ready-to-POST body (memoised; stable identity while the portfolio is unchanged). */
  request: PortfolioIn;
  setPortfolio: (p: Portfolio) => void;
  update: (patch: Partial<Portfolio>) => void;
  setHoldings: (hs: Holding[]) => void;
  reset: () => void;
  shareUrl: () => string;
}

const Ctx = createContext<PortfolioCtx | null>(null);

function initial(): Portfolio {
  try {
    const p = new URLSearchParams(window.location.search).get("p");
    if (p) {
      const dec = decodePortfolio(p);
      if (dec) return dec;
    }
  } catch {
    /* ignore */
  }
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const s = sanitize(JSON.parse(raw));
      if (s) return s;
    }
  } catch {
    /* ignore */
  }
  return DEFAULT_PORTFOLIO;
}

export function PortfolioProvider({ children }: { children: ReactNode }) {
  const [portfolio, setPortfolioState] = useState<Portfolio>(initial);

  // strip ?p= once adopted
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.has("p")) {
      url.searchParams.delete("p");
      window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    }
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(portfolio));
    } catch {
      /* ignore */
    }
  }, [portfolio]);

  const setPortfolio = useCallback((p: Portfolio) => setPortfolioState(p), []);
  const update = useCallback((patch: Partial<Portfolio>) => setPortfolioState((p) => ({ ...p, ...patch })), []);
  const setHoldings = useCallback((hs: Holding[]) => setPortfolioState((p) => ({ ...p, holdings: hs })), []);
  const reset = useCallback(() => setPortfolioState(DEFAULT_PORTFOLIO), []);
  const shareUrl = useCallback(() => {
    const u = new URL(window.location.href);
    u.searchParams.set("p", encodePortfolio(portfolio));
    return u.toString();
  }, [portfolio]);
  const request = useMemo(() => toRequest(portfolio), [portfolio]);

  const value = useMemo(
    () => ({ portfolio, request, setPortfolio, update, setHoldings, reset, shareUrl }),
    [portfolio, request, setPortfolio, update, setHoldings, reset, shareUrl],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePortfolio(): PortfolioCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePortfolio must be used inside <PortfolioProvider>");
  return v;
}
