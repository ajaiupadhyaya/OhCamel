/**
 * F3 "live", pure: how the deck stays current without inventing anything.
 *
 *   - Running jobs come from the job stream, GET /api/jobs/events (SSE, Lane B): a known job's
 *     progress moves on its event; a job that leaves `running` drops off; a job that starts is
 *     unknown to the deck (the event has no kind), so it triggers one GET /api/jobs?state=running
 *     instead of a guessed lamp. While the stream is live the list is re-read only to reconcile
 *     (every 2 min); while it is down the deck polls every 15 s, as before F3.
 *   - Readings (POST /deck/reading) are taken once per quote-cache refresh while the session is
 *     open (quality.quote_cache_s, clamped to 15 s … 5 min), every 5 min while it is closed.
 *   - The intraday Monte Carlo VaR (compute plan M2, kind risk.mc_intraday) arrives as an
 *     optional `mc_var` on the reading. The deck shows it only with its asOf, and marks it stale
 *     after one missed 15-minute run plus slack. Absent is drawn as absent, never as zero.
 *
 * There is no multiplexed /api/stream hub on the server (compute plan D5 was not built), so
 * host metrics and readings are still polled; only jobs stream. live.test.ts runs this without
 * a DOM.
 */
import type { JobRow } from "./compute";

// ------------------------------------------------------------------ jobs stream

export interface JobEvent {
  id: string;
  state: string;
  progress: number | null;
}

export type LinkState = "connecting" | "live" | "down";

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** One `event: job` data line from /api/jobs/events, or null when it is not one. */
export function parseJobEvent(data: string): JobEvent | null {
  let v: unknown;
  try {
    v = JSON.parse(data);
  } catch {
    return null;
  }
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  if (typeof o.id !== "string" || !o.id || typeof o.state !== "string" || !o.state) return null;
  return { id: o.id, state: o.state, progress: finite(o.progress) ? o.progress : null };
}

/**
 * Apply one job event to the held running list. `needsList` asks the caller for one list read:
 * a job started that the deck has never seen, and its kind (the lamp's legend) is only on the list.
 */
export function applyJobEvent(running: JobRow[], e: JobEvent): { jobs: JobRow[]; needsList: boolean } {
  const i = running.findIndex((j) => j.id === e.id);
  if (e.state === "running") {
    if (i < 0) return { jobs: running, needsList: true };
    const cur = running[i];
    if (e.progress == null || cur.progress === e.progress) return { jobs: running, needsList: false };
    const jobs = running.slice();
    jobs[i] = { ...cur, progress: e.progress };
    return { jobs, needsList: false };
  }
  if (i < 0) return { jobs: running, needsList: false };
  return { jobs: running.filter((j) => j.id !== e.id), needsList: false };
}

/** Delay before re-opening a stream that failed `attempt` times in a row: 1, 2, 4 … s, capped at 60 s. */
export function reconnectDelay(attempt: number): number {
  const n = Math.max(0, Math.floor(attempt));
  return Math.min(60_000, 1000 * 2 ** Math.min(n, 16));
}

export const JOBS_POLL_LIVE = 120_000;
export const JOBS_POLL_DOWN = 15_000;
export const JOBS_POLL_ERROR = 5 * 60_000;

/** How often the running-jobs list is read, given the stream's state and whether the last read failed. */
export function jobsPollInterval(link: LinkState, lastReadFailed: boolean): number {
  if (lastReadFailed) return JOBS_POLL_ERROR;
  return link === "live" ? JOBS_POLL_LIVE : JOBS_POLL_DOWN;
}

// ------------------------------------------------------------------ reading cadence

export const READING_MIN = 15_000;
export const READING_MAX = 300_000;

/** Refetch interval for POST /deck/reading: one reading per quote refresh while open, 5 min while closed. */
export function readingInterval(r: { clock?: { is_open?: boolean } | null; quality?: { quote_cache_s?: number } | null } | undefined): number {
  if (!r?.clock?.is_open) return READING_MAX;
  const s = r.quality?.quote_cache_s;
  if (!finite(s) || s <= 0) return READING_MIN;
  return Math.max(READING_MIN, Math.min(READING_MAX, s * 1000));
}

// ------------------------------------------------------------------ intraday Monte Carlo VaR (M2)

/**
 * The reading's optional `mc_var` (wire contract for Lane M's risk.mc_intraday):
 *   { var_usd: number, as_of: string, alpha?: number, horizon_days?: number,
 *     method?: string, paths?: number, artifact_id?: string }
 * var_usd is a positive loss in dollars of the book's notional.
 */
export interface McVar {
  var_usd: number;
  alpha: number | null;
  horizon_days: number | null;
  method: string | null;
  paths: number | null;
  as_of: string;
  artifact_id: string | null;
}

/** 15-minute schedule: stale after one missed run (30 min) plus 5 min slack. */
export const MC_MAX_AGE_S = 35 * 60;
const SKEW_MS = 60_000;

export function mcVarOf(reading: unknown): McVar | null {
  if (!reading || typeof reading !== "object") return null;
  const raw = (reading as { mc_var?: unknown }).mc_var;
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  if (!finite(o.var_usd) || typeof o.as_of !== "string" || !o.as_of) return null;
  return {
    var_usd: o.var_usd,
    alpha: finite(o.alpha) ? o.alpha : null,
    horizon_days: finite(o.horizon_days) ? o.horizon_days : null,
    method: typeof o.method === "string" && o.method ? o.method : null,
    paths: finite(o.paths) ? o.paths : null,
    as_of: o.as_of,
    artifact_id: typeof o.artifact_id === "string" && o.artifact_id ? o.artifact_id : null,
  };
}

/** `MC VAR 99 · 1D · FHS` — a label, not a sentence. */
export function mcLabel(m: McVar): string {
  const parts: string[] = [];
  if (m.alpha != null) parts.push(`${Math.round(m.alpha * 1000) / 10}`);
  const head = ["MC VAR", ...parts].join(" ");
  const rest: string[] = [];
  if (m.horizon_days != null) rest.push(`${m.horizon_days}D`);
  if (m.method) rest.push(m.method.toUpperCase());
  return [head, ...rest].join(" · ");
}

/** True when the MC reading cannot be shown as current: unparseable, from the future, or older than MC_MAX_AGE_S. */
export function mcIsStale(m: McVar, nowMs: number): boolean {
  const t = Date.parse(m.as_of);
  if (!Number.isFinite(t)) return true;
  if (t - nowMs > SKEW_MS) return true;
  return nowMs - t > MC_MAX_AGE_S * 1000;
}
