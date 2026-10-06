/**
 * The lamp panel's compute bank, pure: what the box is doing, from
 *   - GET /api/ops/host            (hostd, compute plan II.6): CPU, steal, memory
 *   - GET /api/jobs?state=running  (Lane B, II.2): one lamp per running job kind.
 *
 * Nothing is invented. A hostd that is not configured, down, or still warming up gives
 * three unknown bars and says which; a jobs read that fails is a "down" lamp, not an
 * idle one. An old reading is never drawn as current: when a refresh fails after an
 * earlier answer (React Query keeps the previous data), or hostd's latest sample is older
 * than STALE_SAMPLES sample intervals, the bank says "stale" and marks the bars so.
 * compute.test.ts runs it without a DOM.
 */

export interface HostLatest {
  t_ms?: number;
  cpu?: number | null;
  steal?: number | null;
  iowait?: number | null;
  load1?: number | null;
  mem_total?: number | null;
  mem_available?: number | null;
  swap_used?: number | null;
  groups?: Record<string, { cpu: number | null; mem: number } | null>;
}
export interface HostWire {
  version?: number;
  interval_s?: number;
  cpus?: number;
  now_ms?: number;
  latest?: HostLatest | null;
}
export interface JobRow {
  id: string;
  kind: string;
  state: string;
  progress?: number | null;
  started_at?: string | null;
}
export interface JobsWire {
  jobs?: JobRow[];
}

/**
 * off = not configured on this server; down = configured, did not answer; warming = no first
 * sample yet; stale = a reading exists but is old (refresh failed, or the sampler stalled).
 */
export type HostState = "ok" | "stale" | "off" | "down" | "warming" | "unknown";
export type BarLevel = "ok" | "near" | "over" | "unknown";

export interface ComputeBar {
  key: "cpu" | "steal" | "mem";
  label: "CPU" | "STL" | "MEM";
  /** The reading as a fraction (may exceed 1); null when it cannot be read. */
  value: number | null;
  /** The value clamped to [0, 1], for the bar's length. */
  fill: number;
  text: string;
  level: BarLevel;
  /** True when the value is the last reading of a host that is no longer answering live. */
  stale: boolean;
}
export interface JobLamp {
  kind: string;
  legend: string;
  count: number;
  /** The least-advanced running job of this kind, 0..1, when any reports progress. */
  progress: number | null;
}
export interface ComputeBankModel {
  host: HostState;
  jobs: "ok" | "stale" | "down" | "unknown";
  bars: ComputeBar[];
  lamps: JobLamp[];
  summary: string;
}

export const NEAR = 0.75;
export const OVER = 0.9;
/** A sample older than this many sampler intervals is stale (hostd samples every interval_s). */
export const STALE_SAMPLES = 3;
/** hostd's default sampler interval, used when the wire omits interval_s. */
export const DEFAULT_INTERVAL_S = 5;

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

function pctText(v: number): string {
  const p = v * 100;
  return `${Math.abs(p) < 10 ? p.toFixed(1) : p.toFixed(0)}%`;
}

function bar(key: ComputeBar["key"], label: ComputeBar["label"], value: number | null, stale: boolean): ComputeBar {
  if (value == null || !finite(value)) return { key, label, value: null, fill: 0, text: "—", level: "unknown", stale };
  const level: BarLevel = value >= OVER ? "over" : value >= NEAR ? "near" : "ok";
  return { key, label, value, fill: Math.max(0, Math.min(1, value)), text: pctText(value), level, stale };
}

/** True when hostd's latest sample cannot be shown as current: undated, or older than STALE_SAMPLES intervals. */
export function sampleIsOld(host: HostWire): boolean {
  const t = host.latest?.t_ms;
  if (!finite(t)) return true;
  if (!finite(host.now_ms)) return false;
  const interval = finite(host.interval_s) && host.interval_s > 0 ? host.interval_s : DEFAULT_INTERVAL_S;
  return host.now_ms - t > STALE_SAMPLES * interval * 1000;
}

/** "warehouse.bars_minute" → "WAREHOUSE BAR…": the printed legend under a lamp. */
export function kindLegend(kind: string): string {
  const words = kind.replace(/[._-]+/g, " ").trim().toUpperCase();
  if (!words) return "?";
  return words.length > 14 ? `${words.slice(0, 13)}…` : words;
}

/**
 * hostStale / jobsStale: the last fetch failed but an earlier reading is still held (pass
 * `query.isError && query.data`). The held reading is drawn, marked stale, never as live.
 */
export function computeBank(input: {
  host?: HostWire | null;
  hostError?: { configured?: boolean } | null;
  hostStale?: boolean;
  jobs?: JobsWire | null;
  jobsError?: boolean;
  jobsStale?: boolean;
}): ComputeBankModel {
  const { host, hostError, hostStale, jobs, jobsError, jobsStale } = input;
  let state: HostState;
  if (hostError) state = hostError.configured === false ? "off" : "down";
  else if (!host) state = "unknown";
  else if (host.latest == null) state = hostStale ? "down" : "warming";
  else state = hostStale || sampleIsOld(host) ? "stale" : "ok";

  const l = state === "ok" || state === "stale" ? host!.latest! : null;
  const old = state === "stale";
  const mem = l && finite(l.mem_total) && l.mem_total > 0 && finite(l.mem_available) ? 1 - l.mem_available / l.mem_total : null;
  const bars = [bar("cpu", "CPU", l?.cpu ?? null, old), bar("steal", "STL", l?.steal ?? null, old), bar("mem", "MEM", mem, old)];

  const byKind = new Map<string, JobLamp>();
  if (!jobsError) {
    for (const j of jobs?.jobs ?? []) {
      if (j.state !== "running" || !j.kind) continue;
      const cur = byKind.get(j.kind) ?? { kind: j.kind, legend: kindLegend(j.kind), count: 0, progress: null };
      cur.count += 1;
      if (finite(j.progress)) cur.progress = cur.progress == null ? j.progress : Math.min(cur.progress, j.progress);
      byKind.set(j.kind, cur);
    }
  }
  const lamps = [...byKind.values()].sort((a, b) => a.kind.localeCompare(b.kind));
  const jobState = jobsError ? "down" : jobs ? (jobsStale ? "stale" : "ok") : "unknown";

  const reading = `CPU ${bars[0].text}, steal ${bars[1].text}, memory ${bars[2].text}`;
  const hostWords: Record<HostState, string> = {
    ok: reading,
    stale: `host telemetry stale, last reading ${reading}`,
    off: "host telemetry not configured",
    down: "host telemetry unavailable",
    warming: "host telemetry warming up",
    unknown: "host telemetry not read yet",
  };
  const n = lamps.reduce((s, x) => s + x.count, 0);
  const running = n === 0 ? "no jobs running" : `${n} job${n === 1 ? "" : "s"} running: ${lamps.map((x) => x.legend).join(", ")}`;
  const jobWords =
    jobState === "down" ? "running jobs could not be read" : jobState === "unknown" ? "running jobs not read yet" : jobState === "stale" ? `running jobs stale, last read ${running}` : running;
  return { host: state, jobs: jobState, bars, lamps, summary: `Compute: ${hostWords[state]}; ${jobWords}.` };
}
