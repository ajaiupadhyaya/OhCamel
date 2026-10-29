/**
 * The Machine's wire: `GET /api/machine` (quant/src/ohcamel_quant/api/routers/machine.py) and the job
 * stream's events (`GET /api/jobs/events`). The server sends semantics, not coordinates: each node's ring
 * and `at` (a section and index, an hour, or the node it sits beside); the page lays the rings out.
 *
 * `m` is typed loosely on purpose: its fields differ by kind, every one of them may be absent, and the
 * page reads each defensively. The keys are listed where the server builds them (machine/graph.py,
 * lenses.py, readouts.py); fixture.ts carries a realistic value of every one.
 */
export type NodeKind = "feed" | "series" | "job" | "product" | "result" | "strategy" | "limit" | "kill" | "book";
export type Ring = "feeds" | "daily" | "filings" | "minute" | "options" | "clock" | "products" | "results" | "strategies" | "limits" | "book";
export type EdgeKind = "pull" | "write" | "read" | "publish" | "result" | "roster" | "signal" | "trade" | "scale" | "limit" | "trip" | "halt";

export interface MNode {
  /** "feed:prices" "d:NVDA" "s:NVDA" "m:NVDA" "o:NVDA" "f:VIXCLS" "ff:FF5" "13f:<cik>" "j:<kind>" "p:<kind>" "p:research"
   *  "c:<strategy>@<universe>" "lm:<model>" "ce:<estimator>" "am:<method>:<horizon>" "st:<slug>" "lim:<name>" "kill" "book" */
  id: string;
  k: NodeKind;
  /** Short label. */
  l: string;
  /** Plain name. */
  n: string;
  ring: Ring;
  at: { sec?: "etf" | "eq" | "fred" | "ff" | "13f"; i?: number; n?: number; of?: string; h?: number; beside?: string };
  m: Record<string, unknown>;
  /** "missing" when the source is absent; `why` then says so in words. */
  status?: "missing";
  why?: string;
}

/** `[from, to, kind]`: indices into `nodes`, in the direction data flows. */
export type Edge = [number, number, EdgeKind];

export type RunState = "queued" | "running" | "done" | "failed" | "cancelled";

/** One run of a clock job, queued, running, or started in the last day. A queued run has `start: null`. */
export interface Run {
  id: string;
  kind: string;
  state: RunState;
  start: string | null;
  end: string | null;
  cpu: number | null;
  progress: number | null;
  msg: string | null;
}

export interface MachineOut {
  asof: string;
  nodes: MNode[];
  edges: Edge[];
  runs: Run[];
  /** Every fire of every job as `[kind, New York hour]`, sorted by kind, then hour. */
  x: { fires: [string, number][] };
  provenance: unknown[];
  /** What is missing, in words. */
  notes: string[];
}

/** The `data` of an `event: job` on `GET /api/jobs/events` (api/routers/jobs.py). */
export interface JobEvent {
  type: "job";
  id: string;
  state: Run["state"];
  progress: number | null;
  message: string | null;
}
