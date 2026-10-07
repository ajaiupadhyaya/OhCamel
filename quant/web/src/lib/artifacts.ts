/**
 * Artifact reads (compute plan II.3): GET /api/artifacts/{kind}/latest returns the newest
 * manifest of a job kind -- bare, or as Lane B's {manifest, stale}. Until a kind has run the
 * read is a 404, and a block renders INSUFFICIENT DATA · NOT YET RUN, never a number.
 *
 *   const q = useArtifact("risk.mc_atlas");
 *   const m = manifestOf(q.data);           // null until it exists
 *   const absent = absentLabel(q.error);    // "NOT YET RUN" | "DATA UNAVAILABLE" | null
 */
import { ApiError, DataUnavailableError, NetworkError } from "./api";
import { useApiQuery } from "./query";
import type { ProvenanceRecord } from "./types";

/** Job kinds the pages read. Lane M owns the producers; names follow compute plan Lane M. */
export const KINDS = {
  atlas: "risk.mc_atlas",
  forecasts: "vol.forecast_league",
  covariance: "cov.league",
  farm: "farm.sweep",
  models: "models.xs_lgbm",
  regimes: "regime.hmm",
  surface: "vol.surface_history",
} as const;

/** Nightly products are stale after a missed night plus slack (Review Focus 1). */
export const NIGHTLY_MAX_AGE_SEC = 36 * 3600;

/**
 * Products held for an owner-approved pre-registration (spec §6). While false, their blocks
 * show AWAITING PRE-REGISTRATION and no result is read or shown. Both were approved by the
 * owner on 2026-10-06 (5863c48); an unfrozen EXP-Q01 universe now reads as the API's own
 * INSUFFICIENT DATA verdict, not as awaiting approval.
 */
export const PREREGISTERED: Record<string, boolean> = {
  [KINDS.models]: true, // EXP-Q01
  [KINDS.regimes]: true, // EXP-Q02
};

/** Lane M's verdicts (plan II.3 as amended in M1). DESCRIPTIVE ONLY: a measurement, not a strategy. */
export type VerdictWord = "PASS" | "FAIL" | "ADVISORY" | "INSUFFICIENT DATA" | "DESCRIPTIVE ONLY";

export interface Manifest {
  id: string;
  kind: string;
  params?: Record<string, unknown>;
  data_asof?: string | null;
  finished_at?: string | null;
  cpu_seconds?: number | null;
  engine?: "rust" | "python";
  verdict?: VerdictWord | null;
  /** One line saying why (e.g. "0 PASS · 38 FAIL · 4 skipped"), or null. */
  verdict_detail?: string | null;
  provenance?: ProvenanceRecord[];
  notes?: string[];
  survivorship?: string | null;
  tables?: string[];
  /** Headline readings a product chooses to surface (label → number or text). */
  headline?: Record<string, number | string | null>;
  /** Set when the read came from Lane B's {manifest, stale}. */
  stale?: boolean;
}

/** The manifest inside a latest-artifact response, or null when there is none. */
export function manifestOf(body: unknown): Manifest | null {
  if (!body || typeof body !== "object") return null;
  const b = body as { manifest?: unknown; stale?: unknown; verdict?: unknown; verdict_detail?: unknown };
  const inner = b.manifest && typeof b.manifest === "object" ? (b.manifest as Record<string, unknown>) : (b as Record<string, unknown>);
  if (typeof inner.id !== "string" || !inner.id) return null;
  const m = { ...inner } as unknown as Manifest;
  if (typeof b.stale === "boolean") m.stale = b.stale;
  // Lane M returns verdict and verdict_detail first, beside the manifest; the manifest's own win.
  if (m.verdict == null && typeof b.verdict === "string") m.verdict = b.verdict as VerdictWord;
  if (m.verdict_detail == null && typeof b.verdict_detail === "string") m.verdict_detail = b.verdict_detail;
  return m;
}

/**
 * The max age a Cell applies to an artifact. The API's `stale` (from schedules.yaml max_age,
 * which knows a monthly product from a nightly one) wins: true is always stale, false never;
 * without it, a missed night plus slack.
 */
export function artifactMaxAge(m: Manifest): number {
  if (m.stale === true) return -1;
  if (m.stale === false) return Infinity;
  return NIGHTLY_MAX_AGE_SEC;
}

/** A read that failed because the thing is absent, as the label to show; null for real errors. */
export function absentLabel(error: unknown): "NOT YET RUN" | "DATA UNAVAILABLE" | null {
  if (error instanceof DataUnavailableError) return "DATA UNAVAILABLE";
  if (error instanceof NetworkError) return null;
  if (error instanceof ApiError && error.status === 404) return "NOT YET RUN";
  return null;
}

export function useArtifact(kind: string, enabled = true) {
  return useApiQuery<unknown>(`/artifacts/${encodeURIComponent(kind)}/latest`, undefined, { enabled, staleTime: 5 * 60_000 });
}

/** One artifact table as the API returns it (`lib/serialize.frame`: column-major). */
export interface Frame {
  index: unknown[];
  columns: string[];
  data: Record<string, unknown[]>;
}

/** A frame as row records; null when the body is not a frame (an empty frame is []). */
export function frameRecords(body: unknown): Record<string, unknown>[] | null {
  if (!body || typeof body !== "object") return null;
  const f = body as Partial<Frame>;
  if (!Array.isArray(f.columns) || !f.data || typeof f.data !== "object") return null;
  const cols = f.columns.map(String);
  if (!cols.every((c) => Array.isArray(f.data![c]))) return null;
  const n = cols.length ? f.data[cols[0]].length : 0;
  return Array.from({ length: n }, (_, i) => Object.fromEntries(cols.map((c) => [c, f.data![c][i] ?? null])));
}

/** GET /api/artifacts/{kind}/{id}/{table} (contract II.3). */
export function useArtifactTable(kind: string, id: string | undefined, table: string, enabled = true) {
  return useApiQuery<unknown, Record<string, unknown>[] | null>(`/artifacts/${encodeURIComponent(kind)}/${encodeURIComponent(id ?? "")}/${encodeURIComponent(table)}`, undefined, {
    enabled: enabled && !!id,
    staleTime: 30 * 60_000,
    select: frameRecords,
  });
}
