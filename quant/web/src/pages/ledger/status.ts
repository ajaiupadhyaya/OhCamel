/**
 * A Ledger product row's live state, from GET /api/artifacts/{kind}/latest: the verdict and data
 * date of the newest artifact, or why there is none. The Ledger states what exists; whether a
 * product has run is read, not written into the page.
 */
import { absentLabel, type Manifest } from "../../lib/artifacts";
import { fmtStamp } from "../../design/stamp";

export interface ProductStatus {
  text: string;
  /** The verdict is FAIL: set in --signal. */
  fail: boolean;
}

export function productStatus(m: Manifest | null, error: unknown, loading: boolean): ProductStatus {
  if (m) {
    const parts = [m.verdict ?? "NO VERDICT", `ASOF ${fmtStamp(m.data_asof)}`];
    if (m.stale === true) parts.push("STALE");
    return { text: parts.join(" · "), fail: m.verdict === "FAIL" };
  }
  if (error) return { text: absentLabel(error) ?? "UNREADABLE", fail: false };
  return { text: loading ? "—" : "NOT YET RUN", fail: false };
}
