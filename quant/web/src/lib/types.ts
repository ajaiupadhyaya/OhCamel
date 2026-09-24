/**
 * Wire types shared with the FastAPI backend (see quant/src/ohcamel_quant/api/serialize.py).
 *
 * - Series  -> {index: [...], values: [...]}          (serialize.series)
 * - Frame   -> {index: [...], columns: [...], data: {col: [...]}}  (serialize.frame, column-major)
 * - records -> [{...}, ...]                           (serialize.records, row-major, for tables)
 * - Dates are ISO strings ("2024-01-31"); NaN/inf arrive as null.
 * - Every payload carries `provenance: Provenance[]` and may carry `notes: string[]`.
 */

export type Scalar = number | string | boolean | null;

export interface SeriesPayload<V = number | null> {
  index: (string | number)[];
  values: V[];
}

export interface FramePayload<V = number | null> {
  index: (string | number)[];
  columns: string[];
  data: Record<string, V[]>;
}

export interface ProvenanceRecord {
  source: string; // "yahoo", "fred", "fixture:alpaca", "sec-edgar", ...
  fetched_at: string; // ISO-8601 UTC
  synthetic?: boolean; // always false in this package
  detail?: Record<string, unknown>;
}

/** Fields every analytic endpoint is expected to include. */
export interface Envelope {
  provenance?: ProvenanceRecord[];
  notes?: string[] | string;
}

/** Portfolio request body (api/models.py PortfolioIn). Weights are DECIMAL fractions. */
export interface Holding {
  ticker: string;
  weight: number;
}
export interface PortfolioIn {
  holdings: Holding[];
  start?: string | null;
  end?: string | null;
  benchmark?: string;
  notional?: number;
}

/** Parameter schema used by strategy/model endpoints and rendered by <ParamForm/>. */
export interface ParamSpec {
  name: string;
  type: "int" | "float" | "number" | "bool" | "boolean" | "str" | "string" | "select" | "choice" | "date" | string;
  default?: unknown;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  choices?: (string | number)[] | null;
  options?: (string | number)[] | null;
  description?: string | null;
  label?: string | null;
  unit?: string | null;
}
