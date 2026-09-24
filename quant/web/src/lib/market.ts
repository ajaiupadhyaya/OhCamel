/**
 * Hooks + defensive normalizers for the /api/market/* endpoints that the shared chrome
 * and components depend on (search, universes, 13F). Each normalizer accepts the
 * documented shape and a few plausible variants, so small backend changes don't blank
 * the UI.
 */
import { useApiQuery } from "./query";
import type { Envelope, ProvenanceRecord } from "./types";

// ------------------------------------------------------------------ search

export interface SearchHit {
  ticker: string;
  name: string;
  cik?: string | number | null;
  exchange?: string | null;
}

export function normalizeSearch(raw: unknown): SearchHit[] {
  const list: unknown[] = Array.isArray(raw)
    ? raw
    : raw && typeof raw === "object"
      ? (((raw as any).results ?? (raw as any).hits ?? (raw as any).data ?? (raw as any).matches ?? []) as unknown[])
      : [];
  return list
    .map((r: any) => {
      if (typeof r === "string") return { ticker: r.toUpperCase(), name: r.toUpperCase() };
      const ticker = String(r?.ticker ?? r?.symbol ?? "").toUpperCase();
      const name = String(r?.name ?? r?.title ?? r?.company ?? ticker);
      return { ticker, name, cik: r?.cik ?? null, exchange: r?.exchange ?? null };
    })
    .filter((h) => h.ticker);
}

export function useTickerSearch(q: string, enabled = true) {
  const query = q.trim();
  return useApiQuery<unknown, SearchHit[]>("/market/search", { q: query }, { enabled: enabled && query.length > 0, staleTime: 60 * 60_000, select: normalizeSearch });
}

// ------------------------------------------------------------------ universes

export interface Universe {
  name: string;
  label: string;
  description?: string;
  tickers: string[];
  /** optional ticker -> group (e.g. sector / asset class) */
  groups?: Record<string, string>;
  /** optional ticker -> display name */
  names?: Record<string, string>;
}

function toUniverse(name: string, v: any): Universe | null {
  if (Array.isArray(v)) return { name, label: prettyName(name), tickers: v.map((t) => String(typeof t === "object" ? t.ticker : t).toUpperCase()) };
  if (!v || typeof v !== "object") return null;
  let tickers: string[] = [];
  const names: Record<string, string> = {};
  const groups: Record<string, string> = {};
  const src = v.tickers ?? v.members ?? v.symbols ?? v.holdings;
  if (Array.isArray(src)) {
    for (const t of src) {
      if (typeof t === "string") tickers.push(t.toUpperCase());
      else if (t && typeof t === "object" && t.ticker) {
        const tk = String(t.ticker).toUpperCase();
        tickers.push(tk);
        if (t.name) names[tk] = String(t.name);
        if (t.group ?? t.sector ?? t.asset_class) groups[tk] = String(t.group ?? t.sector ?? t.asset_class);
      }
    }
  } else if (src && typeof src === "object") {
    // {group: [tickers]}
    for (const [g, ts] of Object.entries(src)) if (Array.isArray(ts)) for (const t of ts) {
        tickers.push(String(t).toUpperCase());
        groups[String(t).toUpperCase()] = g;
      }
  }
  if (v.groups && typeof v.groups === "object" && !Array.isArray(v.groups)) {
    for (const [g, ts] of Object.entries(v.groups)) if (Array.isArray(ts)) for (const t of ts) groups[String(t).toUpperCase()] = g;
  }
  if (v.names && typeof v.names === "object") Object.assign(names, v.names);
  tickers = [...new Set(tickers)];
  const nm = String(v.name ?? v.id ?? name);
  return { name: nm, label: String(v.label ?? v.title ?? prettyName(nm)), description: v.description ?? undefined, tickers, groups: Object.keys(groups).length ? groups : undefined, names: Object.keys(names).length ? names : undefined };
}

export const prettyName = (s: string) => s.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

export function normalizeUniverses(raw: unknown): Universe[] {
  if (!raw) return [];
  let src: any = raw;
  if (!Array.isArray(src) && typeof src === "object" && (src.universes ?? src.results ?? src.data)) src = src.universes ?? src.results ?? src.data;
  const out: Universe[] = [];
  if (Array.isArray(src)) {
    src.forEach((v: any, i: number) => {
      const u = typeof v === "string" ? { name: v, label: prettyName(v), tickers: [] } : toUniverse(String(v?.name ?? v?.id ?? i), v);
      if (u) out.push(u);
    });
  } else if (typeof src === "object") {
    for (const [k, v] of Object.entries(src)) {
      if (k === "provenance" || k === "notes") continue;
      const u = toUniverse(k, v);
      if (u) out.push(u);
    }
  }
  return out;
}

export function useUniverses() {
  return useApiQuery<unknown, Universe[]>("/market/universes", undefined, { staleTime: Infinity, select: normalizeUniverses });
}

// ------------------------------------------------------------------ 13F

export interface Filer {
  cik: string;
  name: string;
  manager?: string;
  description?: string;
}

export function normalizeFilers(raw: unknown): Filer[] {
  let src: any = raw;
  if (src && !Array.isArray(src) && typeof src === "object") src = src.filers ?? src.notable_13f_filers ?? src.results ?? src.data ?? src;
  if (Array.isArray(src)) {
    return src
      .map((f: any) => ({ cik: String(f?.cik ?? f?.CIK ?? "").replace(/^0+/, ""), name: String(f?.name ?? f?.filer ?? f?.manager ?? f?.cik ?? ""), manager: f?.manager ?? undefined, description: f?.description ?? f?.note ?? undefined }))
      .filter((f: Filer) => f.cik);
  }
  if (src && typeof src === "object") {
    return Object.entries(src)
      .filter(([k]) => k !== "provenance" && k !== "notes")
      .map(([k, v]: [string, any]) => (typeof v === "string" ? { cik: /^\d+$/.test(k) ? k : v, name: /^\d+$/.test(k) ? v : k } : { cik: String(v?.cik ?? k), name: String(v?.name ?? k) }));
  }
  return [];
}

export interface Holding13F {
  ticker: string | null;
  issuer: string;
  weight: number; // decimal
  value_usd: number | null;
  shares: number | null;
  put_call: string | null; // option rows ("Put"/"Call") are excluded from imports
}

export interface Filing13F extends Envelope {
  filer: string;
  cik?: string;
  period?: string;
  filed?: string;
  url?: string;
  holdings: Holding13F[];
  summary?: { n_positions?: number; total_value_usd?: number; top10_weight?: number; hhi?: number; effective_n?: number };
}

export function normalize13F(raw: unknown): Filing13F {
  const r: any = raw ?? {};
  let hs: any[] = [];
  const h = r.holdings ?? r.positions ?? r.data;
  if (Array.isArray(h)) hs = h;
  else if (h && Array.isArray(h.index) && h.data) {
    // frame shape
    const cols: string[] = h.columns ?? Object.keys(h.data);
    hs = h.index.map((_: unknown, i: number) => Object.fromEntries(cols.map((c) => [c, h.data[c]?.[i]])));
  }
  const holdings: Holding13F[] = hs.map((x: any) => ({
    ticker: x?.ticker ? String(x.ticker).toUpperCase() : null,
    issuer: String(x?.issuer ?? x?.name ?? x?.nameOfIssuer ?? x?.ticker ?? "?"),
    weight: Number(x?.weight ?? 0),
    value_usd: x?.value_usd ?? x?.value ?? null,
    shares: x?.shares ?? null,
    put_call: x?.put_call ?? null,
  }));
  const total = holdings.reduce((s, x) => s + (x.value_usd ?? 0), 0);
  if (holdings.every((x) => !x.weight) && total > 0) holdings.forEach((x) => (x.weight = (x.value_usd ?? 0) / total));
  holdings.sort((a, b) => b.weight - a.weight);
  return {
    filer: String(r.filer?.name ?? r.filer ?? r.name ?? "13F filer"),
    period: r.period ?? r.report_date ?? undefined,
    filed: r.filed ?? r.filing_date ?? undefined,
    cik: r.cik ?? undefined,
    url: r.url ?? undefined,
    summary: r.summary ?? undefined,
    holdings,
    provenance: r.provenance as ProvenanceRecord[] | undefined,
    notes: r.notes,
  };
}

export function use13FFilers(enabled = true) {
  return useApiQuery<unknown, Filer[]>("/market/13f/filers", undefined, { enabled, staleTime: Infinity, select: normalizeFilers });
}

export function use13F(cik: string | null) {
  // top=200 so the Top-N selector can range freely without refetching
  return useApiQuery<unknown, Filing13F>(`/market/13f/${encodeURIComponent(cik ?? "")}`, { top: 200 }, { enabled: !!cik, select: normalize13F, placeholderData: undefined });
}
