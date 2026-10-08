/**
 * Compute page model (compute plan D12): pure transforms of the compute tier's reads.
 *   GET /api/ops/host      hostd snapshot and its hour of history (contract II.6)
 *   GET /api/jobs          the queue (II.2); GET /api/jobs/events streams state and progress
 *   GET /api/ops/kernels   the Rust vs Python benchmark table (docs/perf/kernels.json)
 * Missing inputs stay missing (null), never a guessed number.
 */

export interface JobRow {
  id: string;
  kind: string;
  state: string;
  submitted_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  progress?: number | null;
  message?: string | null;
  cpu_seconds?: number | null;
  peak_rss_bytes?: number | null;
  artifact_id?: string | null;
  submitted_by?: string | null;
  mem_class?: string | null;
  heavy?: boolean | null;
  attempts?: number | null;
  error?: string | null;
}

export interface JobEvent {
  type: "job";
  id: string;
  state: string;
  progress: number | null;
  message: string | null;
}

export interface HostHistoryPoint {
  t_ms: number;
  cpu?: number | null;
  steal?: number | null;
  mem_available?: number | null;
  groups_cpu?: Record<string, number | null | undefined> | null;
}

export interface HostIn {
  cpus?: number | null;
  latest?: { mem_total?: number | null } | null;
  history?: HostHistoryPoint[] | null;
}

export interface HostSeries {
  t: number[];
  /** Minutes before the latest sample (≤ 0): the x axis of the hour charts. */
  minAgo: number[];
  cpuCores: (number | null)[];
  slices: { name: string; y: (number | null)[] }[];
  steal: (number | null)[];
  memUsedGb: (number | null)[];
}

export interface KernelRow {
  name: string;
  python_ms: number | null;
  rust_1t_ms: number | null;
  rust_2t_ms: number | null;
  measured_on: string | null;
  sha: string | null;
  /** Python ms / Rust 1-thread ms (below 1: Rust is slower). */
  speedup: number | null;
  /** Rust 1-thread ms / Rust 2-thread ms; null for a single-threaded kernel. */
  scaling: number | null;
  /** The engine this server's dispatcher runs the kernel on. */
  engine: "rust" | "python" | null;
}

const PRODUCT_PAGES: Record<string, { to: string; code: string }> = {
  "risk.mc_atlas": { to: "/risk", code: "RISK" },
  "risk.mc_intraday": { to: "/risk", code: "RISK" },
  "vol.forecast_league": { to: "/options?tab=forecasts", code: "VOL" },
  "vol.surface_history": { to: "/options?tab=history", code: "VOL" },
  "cov.league": { to: "/portfolio?tab=cov", code: "PORT" },
  "farm.sweep": { to: "/research?view=farm", code: "RSCH" },
  "models.xs_lgbm": { to: "/research?view=models", code: "RSCH" },
  "regime.hmm": { to: "/macro?tab=regimes", code: "RATES" },
};

/** The page that reads a job kind's artifacts; ingest kinds filter this page; others have none. */
export function pageOf(kind: string): { to: string; code: string } | null {
  if (PRODUCT_PAGES[kind]) return PRODUCT_PAGES[kind];
  if (kind.startsWith("ingest.")) return { to: `/compute?kind=${kind}`, code: "DATA" };
  return null;
}

/** "ohcamel-batch.slice" → "BATCH". */
/** Ticks every 15 minutes back from the latest sample, labelled −60 … NOW (minutes). */
export const HOUR_TICKS = [-60, -45, -30, -15, 0].map((m) => ({ at: m, label: m === 0 ? "NOW" : String(m) }));

export function sliceLabel(name: string): string {
  return name.replace(/\.slice$/, "").replace(/^ohcamel-/, "").toUpperCase();
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function hostSeries(host: HostIn | null | undefined): HostSeries | null {
  const h = host?.history ?? [];
  if (!h.length) return null;
  const cpus = num(host?.cpus);
  const total = num(host?.latest?.mem_total);
  const names: string[] = [];
  for (const p of h) for (const k of Object.keys(p.groups_cpu ?? {})) if (!names.includes(k)) names.push(k);
  const last = h[h.length - 1].t_ms;
  return {
    t: h.map((p) => p.t_ms),
    minAgo: h.map((p) => (p.t_ms - last) / 60_000),
    cpuCores: h.map((p) => (num(p.cpu) != null && cpus != null ? (p.cpu as number) * cpus : null)),
    slices: names.map((k) => ({ name: sliceLabel(k), y: h.map((p) => num(p.groups_cpu?.[k])) })),
    steal: h.map((p) => num(p.steal)),
    memUsedGb: h.map((p) => (total != null && num(p.mem_available) != null ? (total - (p.mem_available as number)) / 1e9 : null)),
  };
}

/** Jobs that finished within 24 hours of `nowMs`, newest first. */
export function last24h(rows: JobRow[], nowMs: number): JobRow[] {
  const since = nowMs - 24 * 3600_000;
  return rows
    .filter((r) => r.finished_at && Date.parse(r.finished_at) >= since)
    .sort((a, b) => Date.parse(b.finished_at!) - Date.parse(a.finished_at!));
}

/** Fold one SSE event into the list. A new job or a state change needs a refetch (new fields). */
export function applyEvent(rows: JobRow[], e: JobEvent): { rows: JobRow[]; refetch: boolean } {
  const i = rows.findIndex((r) => r.id === e.id);
  if (i < 0) return { rows, refetch: true };
  const r = rows[i];
  const next: JobRow = { ...r, state: e.state, progress: e.progress ?? r.progress, message: e.message ?? r.message };
  const out = rows.slice();
  out[i] = next;
  return { rows: out, refetch: e.state !== r.state };
}

export function kernelRows(body: unknown): KernelRow[] {
  const b = body && typeof body === "object" ? (body as Record<string, unknown>) : {};
  const list = Array.isArray(b.kernels) ? (b.kernels as Record<string, unknown>[]) : [];
  const engines = (b.engines && typeof b.engines === "object" ? b.engines : {}) as Record<string, unknown>;
  return list.map((k) => {
    const py = num(k.python_ms);
    const r1 = num(k.rust_1t_ms);
    const r2 = num(k.rust_2t_ms);
    const name = String(k.name ?? "");
    const eng = engines[name];
    return {
      name,
      python_ms: py,
      rust_1t_ms: r1,
      rust_2t_ms: r2,
      measured_on: typeof k.measured_on === "string" ? k.measured_on : null,
      sha: typeof k.sha === "string" ? k.sha : null,
      speedup: py != null && r1 ? py / r1 : null,
      scaling: r1 != null && r2 ? r1 / r2 : null,
      engine: eng === "rust" || eng === "python" ? eng : null,
    };
  });
}

export function measuredOn(rows: KernelRow[]): string[] {
  return [...new Set(rows.map((r) => r.measured_on).filter((m): m is string => !!m))];
}

/** One SSE `data:` line, or null when it is not a job event. */
export function parseJobEvent(data: string): JobEvent | null {
  let v: unknown;
  try {
    v = JSON.parse(data);
  } catch {
    return null;
  }
  if (!v || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  if (o.type !== "job" || typeof o.id !== "string" || typeof o.state !== "string") return null;
  return { type: "job", id: o.id, state: o.state, progress: num(o.progress), message: typeof o.message === "string" ? o.message : null };
}
