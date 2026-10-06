/**
 * Compute (/compute) — shell. Four cells over the compute tier's reads, each rendering
 * INSUFFICIENT DATA · NOT YET RUN (404) or · DATA UNAVAILABLE (503) until its producer ships:
 *   HOST      GET /api/ops/host          (hostd, compute plan II.6)
 *   JOBS      GET /api/jobs?limit=&kind= (Lane B, II.2)
 *   SCHEDULE  GET /api/jobs/schedules    (Lane B, schedules.yaml)
 *   KERNELS   GET /api/ops/kernels       (Lane A benchmark table)
 * Lane P2 (P2-7) completes it: SSE progress, last 24h with cpu_seconds and peak RSS.
 */
import { useSearchParams } from "react-router-dom";
import { DataTable, Page, ReadCell, StatGrid, StatTile, type Column } from "../components";
import { fmtStamp } from "../design/stamp";
import { fmtMultiple, fmtNum, fmtPct } from "../lib/format";
import { useApiQuery } from "../lib/query";
import type { Envelope } from "../lib/types";

interface HostFull extends Envelope {
  cpus?: number;
  latest?: {
    t_ms?: number;
    cpu?: number | null;
    steal?: number | null;
    iowait?: number | null;
    load1?: number | null;
    mem_total?: number | null;
    mem_available?: number | null;
    swap_used?: number | null;
    groups?: Record<string, { cpu?: number | null; mem?: number | null }>;
  } | null;
}

interface JobRow {
  id: string;
  kind: string;
  state: string;
  submitted_at?: string | null;
  finished_at?: string | null;
  progress?: number | null;
  cpu_seconds?: number | null;
}

interface ScheduleRow {
  name: string;
  cron?: string;
  kind?: string;
  next_run?: string | null;
}

interface KernelRow {
  name: string;
  python_ms?: number | null;
  rust_1t_ms?: number | null;
  rust_2t_ms?: number | null;
  measured_on?: string | null;
  sha?: string | null;
}

/** A list endpoint's rows: a bare array or `{<key>: [...]}`. */
function listOf<T>(body: unknown, key: string): T[] {
  if (Array.isArray(body)) return body as T[];
  const v = body && typeof body === "object" ? (body as Record<string, unknown>)[key] : undefined;
  return Array.isArray(v) ? (v as T[]) : [];
}

const bytes = (v: number | null | undefined) => (v == null ? "—" : `${fmtNum(v / 1e9, 2)} GB`);

const JOB_COLS: Column<JobRow>[] = [
  { key: "kind", label: "KIND", render: (r) => <span className="num">{r.kind}</span> },
  { key: "state", label: "STATE", render: (r) => <span className={/fail|error/i.test(r.state) ? "num loss" : "num"}>{r.state.toUpperCase()}</span> },
  { key: "submitted_at", label: "SUBMITTED", align: "right", hideBelow: 600, render: (r) => <span className="num">{fmtStamp(r.submitted_at ?? null)}</span> },
  { key: "progress", label: "PROG", numeric: true, format: (v) => fmtPct(v, 0) },
  { key: "cpu_seconds", label: "CPU S", numeric: true, format: (v) => fmtNum(v, 1) },
];

const SCHED_COLS: Column<ScheduleRow>[] = [
  { key: "name", label: "NAME", render: (r) => <span className="num">{r.name}</span> },
  { key: "cron", label: "CRON NY", hideBelow: 600, render: (r) => <span className="num">{r.cron ?? "—"}</span> },
  { key: "next_run", label: "NEXT", align: "right", render: (r) => <span className="num">{fmtStamp(r.next_run ?? null)}</span> },
];

const KERNEL_COLS: Column<KernelRow>[] = [
  { key: "name", label: "KERNEL", render: (r) => <span className="num">{r.name}</span> },
  { key: "python_ms", label: "PYTHON MS", numeric: true, format: (v) => fmtNum(v, 1) },
  { key: "rust_1t_ms", label: "RUST 1T MS", numeric: true, format: (v) => fmtNum(v, 1) },
  { key: "rust_2t_ms", label: "RUST 2T MS", numeric: true, format: (v) => fmtNum(v, 1), hideBelow: 600 },
  { key: "speedup", label: "×", numeric: true, value: (r) => (r.python_ms && r.rust_1t_ms ? r.python_ms / r.rust_1t_ms : null), format: (v) => fmtMultiple(v, 1) },
  { key: "sha", label: "SHA", align: "right", hideBelow: 900, render: (r) => <span className="num">{r.sha ? r.sha.slice(0, 7) : "—"}</span> },
];

export default function Compute() {
  const [params] = useSearchParams();
  const kind = params.get("kind")?.toLowerCase() || undefined;
  const host = useApiQuery<HostFull>("/ops/host", undefined, { refetchInterval: (q) => (q.state.status === "error" ? 5 * 60_000 : 15_000) });
  const jobs = useApiQuery<unknown>("/jobs", { limit: 25, kind });
  const sched = useApiQuery<unknown>("/jobs/schedules");
  const kernels = useApiQuery<unknown>("/ops/kernels");

  return (
    <Page title="Compute" meta={kind ? <span>KIND {kind.toUpperCase()}</span> : undefined}>
      <div className="grid-2">
        <ReadCell<HostFull> title="HOST · HOSTD" query={host} source="GET /api/ops/host" span="all" skeletonHeight={96}>
          {(d) => {
            const l = d.latest;
            const used = l?.mem_total != null && l.mem_available != null ? l.mem_total - l.mem_available : null;
            return (
              <StatGrid min={140}>
                <StatTile size="md" label="CPU" value={fmtPct(l?.cpu, 0)} caption={d.cpus ? `${d.cpus} VCPU` : undefined} />
                <StatTile size="md" label="STEAL" value={fmtPct(l?.steal, 1)} tone={(l?.steal ?? 0) > 0.05 ? "loss" : "neutral"} />
                <StatTile size="md" label="IOWAIT" value={fmtPct(l?.iowait, 1)} />
                <StatTile size="md" label="MEM USED" value={bytes(used)} caption={l?.mem_total != null ? `OF ${bytes(l.mem_total)}` : undefined} />
                <StatTile size="md" label="SWAP" value={bytes(l?.swap_used)} tone={(l?.swap_used ?? 0) > 0 ? "loss" : "neutral"} />
                <StatTile size="md" label="LOAD 1M" value={fmtNum(l?.load1, 2)} />
              </StatGrid>
            );
          }}
        </ReadCell>
        <ReadCell<unknown> title={kind ? `JOBS · ${kind.toUpperCase()}` : "JOBS"} query={jobs} source={`GET /api/jobs?limit=25${kind ? `&kind=${kind}` : ""}`} flush skeletonHeight={160}>
          {(d) => <DataTable<JobRow> columns={JOB_COLS} rows={listOf<JobRow>(d, "jobs")} rowKey={(r) => r.id} empty="NO JOBS" />}
        </ReadCell>
        <ReadCell<unknown> title="SCHEDULE" query={sched} source="GET /api/jobs/schedules" flush skeletonHeight={160}>
          {(d) => <DataTable<ScheduleRow> columns={SCHED_COLS} rows={listOf<ScheduleRow>(d, "schedules")} rowKey={(r) => r.name} empty="NO SCHEDULES" />}
        </ReadCell>
        <ReadCell<unknown> title="KERNELS · RUST VS PYTHON" query={kernels} source="GET /api/ops/kernels" span="all" flush skeletonHeight={160}>
          {(d) => <DataTable<KernelRow> columns={KERNEL_COLS} rows={listOf<KernelRow>(d, "kernels")} rowKey={(r) => r.name} empty="NO BENCHMARKS" />}
        </ReadCell>
      </div>
    </Page>
  );
}
