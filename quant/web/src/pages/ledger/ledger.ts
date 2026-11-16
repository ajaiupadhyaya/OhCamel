/**
 * The Ledger's wire shape (GET /api/ledger, quant/src/ohcamel_quant/machine/ledger.py) and the pure helpers the
 * page and the System index use. The rows are not written here: prose rows come from machine/ledger.yaml with the
 * date each was last checked, and the advisory, limit and owner state is computed by the server from the backup's
 * record, the latest desk report, the hosts' builds and the pre-registrations each time it is read.
 */
import { ApiError, DataUnavailableError, NetworkError } from "../../lib/api";
import { fmtStamp } from "../../design/stamp";

export type LedgerSection = "BUILT" | "CUT" | "ADVISORY" | "LIMITS" | "OWNER";
/** static: a prose row; ok / attention / unknown: a row the server computed from its source. */
export type RowState = "static" | "ok" | "attention" | "unknown";

export interface LedgerRow {
  section: LedgerSection;
  item: string;
  detail: string;
  state: RowState;
  /** Computed rows: where the server read it. */
  source: string;
  /** Prose rows: the ISO date last checked against the code. */
  checked: string | null;
  /** Prose rows checked more than `stale_after_days` before `as_of`. */
  stale: boolean;
  /** Products: the job kind whose latest artifact gives the row its live state. */
  kind: string | null;
  /** Products: the Methodology entry id. */
  methodology: string | null;
}

export interface LedgerOut {
  as_of: string;
  stale_after_days: number;
  rows: LedgerRow[];
  provenance: unknown[];
  notes: string[];
}

export const SECTION_ORDER: LedgerSection[] = ["BUILT", "CUT", "ADVISORY", "LIMITS", "OWNER"];

export type NumberedRow = LedgerRow & { n: number };

/** The rows of each section in the server's order, numbered within it; a section the page does not know is dropped. */
export function bySection(rows: LedgerRow[]): { section: LedgerSection; rows: NumberedRow[] }[] {
  return SECTION_ORDER.map((section) => ({
    section,
    rows: rows.filter((r) => r.section === section).map((r, i) => ({ ...r, n: i + 1 })),
  }));
}

/** The mono line under a row's item: when a prose row was checked (and STALE), or a computed row's state and source. */
export function rowMark(r: LedgerRow): { text: string; cls: string } {
  if (r.state === "static") {
    return { text: `CHECKED ${fmtStamp(r.checked)}${r.stale ? " · STALE" : ""}`, cls: r.stale ? "lg-mark lg-mark-stale" : "lg-mark" };
  }
  const word = { ok: "READ", attention: "ATTENTION", unknown: "UNKNOWN" }[r.state];
  return { text: `${word} · ${r.source}`, cls: `lg-mark lg-mark-${r.state}` };
}

/** Why the ledger could not be read, in the site's words. */
export function ledgerAbsent(error: unknown): string {
  if (error instanceof DataUnavailableError) return "DATA UNAVAILABLE";
  if (error instanceof NetworkError) return "API UNREACHABLE";
  if (error instanceof ApiError && error.status === 404) return "NOT SERVED";
  return "UNREADABLE";
}

/** The System index's one line for the Ledger. */
export function ledgerStatus(data: LedgerOut | undefined, error: unknown, loading: boolean): string {
  if (data) {
    const count = (s: RowState) => data.rows.filter((r) => r.state === s).length;
    const parts = [`AS OF ${fmtStamp(data.as_of)}`, `${data.rows.length} ENTRIES`];
    if (count("attention")) parts.push(`${count("attention")} ATTENTION`);
    if (count("unknown")) parts.push(`${count("unknown")} UNKNOWN`);
    const stale = data.rows.filter((r) => r.stale).length;
    if (stale) parts.push(`${stale} STALE`);
    return parts.join(" · ");
  }
  if (loading) return "LEDGER …";
  return `LEDGER ${ledgerAbsent(error)}`;
}
