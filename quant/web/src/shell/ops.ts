/**
 * The shell's reads: GET /api/ops (build, engine mode, jobs, session clock) and
 * GET /api/ops/host (CPU). Every field is optional and every read may fail (404 before
 * the route exists, 503 when its source is down); readouts then show a dash and a
 * hatched lamp, never a guess.
 */
import type { ReadingClock } from "../pages/deck/model";
import { useApiQuery } from "../lib/query";
import type { Session } from "./dateline";

export interface OpsOut {
  mode?: string | null;
  build?: { git_sha?: string | null; version?: string | null } | null;
  jobs?: { queued?: number; running?: number; done_24h?: number; failed_24h?: number } | null;
  /** The session clock, in the same shape the Flight Deck reads from /deck/reading. */
  clock?: ReadingClock | null;
  desk?: { clock?: ReadingClock | null } | null;
}

export interface HostOut {
  latest?: { cpu?: number | null } | null;
}

const POLL = 60_000;
const POLL_ERR = 5 * 60_000;

export function useOps() {
  return useApiQuery<OpsOut>("/ops", undefined, {
    staleTime: 30_000,
    refetchInterval: (q) => (q.state.status === "error" ? POLL_ERR : POLL),
  });
}

export function useHost() {
  return useApiQuery<HostOut>("/ops/host", undefined, {
    staleTime: 15_000,
    refetchInterval: (q) => (q.state.status === "error" ? POLL_ERR : 30_000),
  });
}

/** The session as the clock on the wire states it; no clock → "unknown". */
export function sessionOf(ops: OpsOut | undefined): Session {
  const clock = ops?.clock ?? ops?.desk?.clock ?? null;
  if (!clock || typeof clock.is_open !== "boolean") return "unknown";
  return clock.is_open ? "open" : "closed";
}
