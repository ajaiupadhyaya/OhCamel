/**
 * One model for three sources. Every instrument on the Flight Deck takes a DeckModel, built
 * in the browser from:
 *   - POST /deck/reading (your portfolio, or the reference book)  → fromReading()
 *   - GET  /engine/snapshot (the OCaml engine's read-only bridge)  → fromEngine()
 * and the session tape takes TapeColumns, from GET /deck/tape, the page's own trail of
 * readings (appendTrail → trailToTape) or GET /engine/history (historyToTape).
 *
 * Pure: model.test.ts runs it without a DOM. Wire shapes are the contract in
 * docs/superpowers/specs/2026-09-24-quant-flight-deck-design.md §API.
 */
import type { Envelope, ProvenanceRecord } from "../../lib/types";
import type { History, Snapshot } from "../engine/types";
import type { BlipIn } from "./radar";
import type { ScopeLimit } from "./scope";

// ------------------------------------------------------------------ wire: /deck/reading

export type FeedState = "ok" | "stale" | "down" | "off" | "unknown";
export type DeckSource = "portfolio" | "reference" | "engine";

export interface ReadingClock {
  session?: string;
  is_open: boolean;
  next_open: string | null;
  next_close: string | null;
  source: string;
  note: string | null;
}
export interface ReadingBook {
  notional: number;
  equity_usd: number | null;
  day_pnl: number | null;
  day_pnl_usd: number | null;
  gross: number | null;
  net: number | null;
  cash: number | null;
  observations: number | null;
  start: string | null;
  end: string | null;
}
export interface ReadingRisk {
  alpha: number;
  var: number | null;
  es: number | null;
  var_usd: number | null;
  es_usd: number | null;
  hist_var: number | null;
  hist_es: number | null;
  model: string | null;
  ewma_lambda: number | null;
}
export interface ReadingMark {
  ticker: string;
  price: number | null;
  prev_close: number | null;
  change_pct: number | null;
  as_of: string | null;
  age_s: number | null;
  source: string | null;
  weight: number | null;
  live_weight: number | null;
  error: string | null;
}
export interface ReadingBlip extends BlipIn {
  component_var_usd: number | null;
}
export interface ReadingLimit extends ScopeLimit {
  kind: string | null;
  scope: string;
}
export interface ReadingUnevaluated {
  name: string;
  kind: string | null;
  reason: string;
}
export interface ReadingFeed {
  key: string;
  label: string;
  state: FeedState;
  detail: string | null;
  age_s: number | null;
}
export interface ReadingQuality {
  complete: boolean;
  missing: string[];
  offline: boolean;
  fixtures?: boolean;
  basis: string;
  history_as_of: string;
  quote_cache_s: number;
}
export interface Reading extends Envelope {
  quality?: ReadingQuality;
  as_of: string;
  source: string;
  clock: ReadingClock;
  book: ReadingBook;
  risk: ReadingRisk;
  marks: ReadingMark[];
  blips: ReadingBlip[];
  limits: ReadingLimit[];
  unevaluated: ReadingUnevaluated[];
  feeds: ReadingFeed[];
}

// ------------------------------------------------------------------ wire: /deck/books, /deck/tape

export interface RecorderStatus {
  enabled: boolean;
  running: boolean;
  db_path?: string | null; // the spec's name; the server sends `db`
  db?: string | null;
  last_write: string | null;
  last_tick?: string | null;
  last_error?: string | null;
  sessions: number | null;
  rows: number | null;
  interval_s: number | null;
  keep_sessions?: number | null;
}
export interface ReferenceBook {
  key: string;
  name?: string;
  holdings: { ticker: string; weight: number }[];
  notional?: number;
  benchmark?: string;
  start?: string | null;
  end?: string | null;
  limits?: unknown; // LimitIn[] — this book's defaults, if the server sends them per book
}
export interface BooksOut extends Envelope {
  books: ReferenceBook[];
  /** LimitIn[] — the server's default limits (what `limits: null` means); sanitised before use. */
  default_limits?: unknown;
  recorder: RecorderStatus;
}

export interface TapeOut extends Envelope {
  book?: string;
  session?: string | null;
  n: number;
  ts: string[];
  day_pnl_usd: (number | null)[];
  var_usd: (number | null)[];
  es_usd: (number | null)[];
  utilisation: Record<string, (number | null)[]>;
  change_pct?: Record<string, (number | null)[]>;
}

// ------------------------------------------------------------------ the model

export interface DeckModel {
  observation_key?: string;
  quality?: ReadingQuality;
  source: DeckSource;
  as_of: string | null;
  clock: ReadingClock | null;
  notional: number | null;
  alpha: number | null;
  counters: {
    day_pnl: number | null;
    day_pnl_usd: number | null;
    var_usd: number | null;
    es_usd: number | null;
    /** The oldest mark's age, seconds — "how stale is the least fresh price". */
    marks_age_s: number | null;
  };
  limits: ReadingLimit[];
  unevaluated: ReadingUnevaluated[];
  blips: ReadingBlip[];
  feeds: ReadingFeed[];
  marks: ReadingMark[];
  notes: string[];
  provenance: ProvenanceRecord[];
}

const finite = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const noteList = (n: string[] | string | null | undefined): string[] => (!n ? [] : Array.isArray(n) ? n.filter((s) => typeof s === "string" && s.trim()) : [n]);

function maxAge(ages: (number | null | undefined)[]): number | null {
  const ok = ages.filter(finite);
  return ok.length ? Math.max(...ok) : null;
}

/** Engine timestamps arrive as "YYYY-MM-DD HH:MM:SS" (UTC) as well as ISO. */
export function parseTs(s: string | null | undefined): number | null {
  if (!s) return null;
  let t = s.trim().replace(" ", "T");
  if (/^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?$/.test(t)) t += "Z";
  const ms = Date.parse(t);
  return Number.isFinite(ms) ? ms : null;
}

export function fromReading(r: Reading, source: Exclude<DeckSource, "engine"> = "portfolio"): DeckModel {
  const marks = r.marks ?? [];
  return {
    source,
    quality: r.quality,
    observation_key: marks.length ? JSON.stringify([marks.map(m => [m.ticker, m.as_of, m.price, m.error]), r.risk?.var_usd, r.risk?.es_usd, r.limits, r.unevaluated]) : undefined,
    as_of: r.as_of ?? null,
    clock: r.clock ?? null,
    notional: r.book?.notional ?? null,
    alpha: r.risk?.alpha ?? null,
    counters: {
      day_pnl: r.book?.day_pnl ?? null,
      day_pnl_usd: r.book?.day_pnl_usd ?? null,
      var_usd: r.risk?.var_usd ?? null,
      es_usd: r.risk?.es_usd ?? null,
      marks_age_s: maxAge(marks.filter((m) => !m.error).map((m) => m.age_s)),
    },
    limits: r.limits ?? [],
    unevaluated: r.unevaluated ?? [],
    blips: r.blips ?? [],
    feeds: r.feeds ?? [],
    marks,
    notes: noteList(r.notes),
    provenance: r.provenance ?? [],
  };
}

/**
 * The engine snapshot mapped onto the same model. Limits pass through (the engine's wire
 * shape is the one the deck adopted); `unevaluated` names become {name, reason}; each
 * position is a blip with its Euler `risk_share` as its share of VaR and exposure / equity as
 * its weight; each fed symbol is a lamp. The engine has no session clock and no day P&L.
 */
export function fromEngine(out: { snapshot: Snapshot } & Envelope, now: number = Date.now()): DeckModel {
  const s = out.snapshot;
  const equity = finite(s.equity) && s.equity !== 0 ? s.equity : null;
  const ageOf = (iso: string | null | undefined) => {
    const t = parseTs(iso);
    return t == null ? null : Math.max(0, Math.round((now - t) / 1000));
  };
  const feedSyms = s.feed?.symbols ?? [];
  const tickOf = new Map(feedSyms.map((f) => [f.symbol, f.last_tick]));
  const weightOf = (p: Snapshot["positions"][number]) => (equity != null && finite(p.exposure) ? p.exposure / equity : (p.weight ?? null));

  const blips: ReadingBlip[] = s.positions.map((p) => ({
    ticker: p.symbol,
    live_weight: weightOf(p),
    change_pct: null,
    component_var: p.component_var,
    component_var_usd: p.component_var,
    pct_var: p.risk_share,
  }));
  const marks: ReadingMark[] = s.positions.map((p) => {
    const tick = tickOf.get(p.symbol) ?? null;
    return { ticker: p.symbol, price: p.price, prev_close: null, change_pct: null, as_of: tick, age_s: ageOf(tick), source: "ohcamel-engine", weight: weightOf(p), live_weight: weightOf(p), error: null };
  });
  const feeds: ReadingFeed[] = [
    { key: "engine", label: "Engine bridge", state: s.warming_up ? "stale" : "ok", detail: s.warming_up ? "answering; covariance window warming up" : "answering", age_s: ageOf(s.as_of) },
    ...feedSyms.map((f) => ({
      key: `feed:${f.symbol}`,
      label: f.symbol,
      state: (f.never_seen ? "down" : f.stale ? "stale" : "ok") as FeedState,
      detail: f.never_seen ? "never seen a tick" : f.stale ? "stale" : "ticking",
      age_s: ageOf(f.last_tick),
    })),
  ];
  const notes = noteList(out.notes);
  notes.push("The engine publishes no session clock and no day P&L, so those counters are blank and no move colours the radar; blips are placed by the engine's Euler risk share.");
  return {
    source: "engine",
    as_of: s.as_of ?? null,
    clock: null,
    notional: equity,
    alpha: null,
    counters: {
      day_pnl: null,
      day_pnl_usd: null,
      var_usd: s.value_at_risk_notional ?? null,
      es_usd: s.expected_shortfall_notional ?? null,
      marks_age_s: maxAge(feedSyms.filter((f) => !f.never_seen).map((f) => ageOf(f.last_tick))),
    },
    limits: (s.limits ?? []).map((l) => ({ ...l, kind: null })),
    unevaluated: (s.unevaluated ?? []).map((name) => ({ name, kind: null, reason: "cannot be evaluated" })),
    blips,
    feeds,
    marks,
    notes,
    provenance: out.provenance ?? [],
  };
}

// ------------------------------------------------------------------ the session tape

/** Columns the tape chart draws — the /deck/tape shape plus a label for the P&L line. */
export interface TapeColumns {
  ts: string[];
  day_pnl_usd: (number | null)[];
  var_usd: (number | null)[];
  es_usd: (number | null)[];
  utilisation: Record<string, (number | null)[]>;
  pnlLabel: string;
}

export interface TrailPoint {
  observation_key?: string;
  ts: string;
  day_pnl_usd: number | null;
  var_usd: number | null;
  es_usd: number | null;
  utilisation: Record<string, number | null>;
}

export const TRAIL_MAX = 2000;

/** Add a reading to the page's own trail; a reading already on it (same as_of) is skipped. */
export function appendTrail(trail: TrailPoint[], m: DeckModel, max: number = TRAIL_MAX): TrailPoint[] {
  if (!m.as_of) return trail;
  const observationKey = m.observation_key ? JSON.stringify([m.observation_key, m.counters.day_pnl_usd, m.counters.var_usd, m.counters.es_usd, m.limits, m.unevaluated]) : undefined;
  if (observationKey && trail.at(-1)?.observation_key === observationKey) return trail;
  if (trail.some((p) => p.ts === m.as_of)) return trail;
  const u: Record<string, number | null> = {};
  for (const l of m.limits) u[l.name] = finite(l.utilisation) ? l.utilisation : null;
  for (const x of m.unevaluated) u[x.name] = null;
  const next = [...trail, { observation_key: observationKey, ts: m.as_of, day_pnl_usd: m.counters.day_pnl_usd, var_usd: m.counters.var_usd, es_usd: m.counters.es_usd, utilisation: u }];
  next.sort((a, b) => (parseTs(a.ts) ?? 0) - (parseTs(b.ts) ?? 0));
  return next.length > max ? next.slice(next.length - max) : next;
}

/** A trail as columns; a limit missing from a point (added later, or renamed) is a gap. */
export function trailToTape(trail: TrailPoint[]): TapeColumns {
  const names: string[] = [];
  for (const p of trail) for (const k of Object.keys(p.utilisation)) if (!names.includes(k)) names.push(k);
  const utilisation: Record<string, (number | null)[]> = {};
  for (const k of names) utilisation[k] = trail.map((p) => (k in p.utilisation ? p.utilisation[k] : null));
  return { ts: trail.map((p) => p.ts), day_pnl_usd: trail.map((p) => p.day_pnl_usd), var_usd: trail.map((p) => p.var_usd), es_usd: trail.map((p) => p.es_usd), utilisation, pnlLabel: "Day P&L" };
}

export function tapeFromServer(t: TapeOut): TapeColumns {
  return { ts: t.ts ?? [], day_pnl_usd: t.day_pnl_usd ?? [], var_usd: t.var_usd ?? [], es_usd: t.es_usd ?? [], utilisation: t.utilisation ?? {}, pnlLabel: "Day P&L" };
}

/**
 * The engine's in-memory history. It has no day P&L and no per-limit utilisation, so the
 * P&L line is the change in equity since the history's first point (labelled as such) and
 * the utilisation block is empty.
 */
export function historyToTape(h: History): TapeColumns {
  const e0 = h.equity.find(finite) ?? null;
  return {
    ts: h.time.map((t) => new Date(t).toISOString()),
    day_pnl_usd: h.equity.map((e) => (finite(e) && e0 != null ? e - e0 : null)),
    var_usd: h.var_notional,
    es_usd: h.es_notional,
    utilisation: {},
    pnlLabel: "Equity change since the trail began",
  };
}
