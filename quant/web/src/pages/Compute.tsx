/**
 * Compute (/compute) -- the droplet's compute tier (compute plan D12):
 *   HOST     GET /api/ops/host       hostd: CPU by slice, steal, memory, swap; an hour of history
 *   QUEUE    GET /api/ops            job counts and CPU seconds of the last 24 h (B6)
 *   JOBS     GET /api/jobs           running jobs with progress (live from GET /api/jobs/events,
 *                                    SSE), the last 24 h with cpu_seconds and peak RSS, each
 *                                    artifact linked to the page that reads it
 *   SCHEDULE GET /api/jobs/schedules not served by this build: the cell says so
 *   KERNELS  GET /api/ops/kernels    Rust vs Python benchmark table (docs/perf/kernels.json)
 * 404 → INSUFFICIENT DATA · NOT YET RUN, 503 → · DATA UNAVAILABLE; never a stand-in number.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { XYChart } from "../charts/XYChart";
import { DataTable, Panel, Page, ReadCell, StatGrid, StatTile, type Column } from "../components";
import { Absent, Lamp } from "../design";
import { fmtStamp } from "../design/stamp";
import { ApiError } from "../lib/api";
import { fmtMultiple, fmtNum, fmtPct } from "../lib/format";
import { queryClient, useApiQuery } from "../lib/query";
import type { Envelope } from "../lib/types";
import { useOps, type OpsOut } from "../shell/ops";
import { HOUR_TICKS, applyEvent, hostSeries, kernelRows, last24h, measuredOn, pageOf, sliceLabel, type HostIn, type JobEvent, type JobRow, type KernelRow } from "./compute/model";
import { useJobEvents, type Link as StreamLink } from "./compute/useJobEvents";
import "./compute/compute.css";

interface HostFull extends Envelope, HostIn {
  latest?: {
    t_ms?: number;
    cpu?: number | null;
    steal?: number | null;
    iowait?: number | null;
    load1?: number | null;
    mem_total?: number | null;
    mem_available?: number | null;
    swap_used?: number | null;
    groups?: Record<string, { cpu?: number | null; mem?: number | null } | null>;
  } | null;
}

interface JobsOut extends Envelope {
  jobs?: JobRow[];
}

interface SliceRow {
  name: string;
  cpu: number | null;
  mem: number | null;
}

const gb = (v: number | null | undefined) => (v == null ? "—" : `${fmtNum(v / 1e9, 2)} GB`);
const mb = (v: number | null | undefined) => (v == null ? "—" : fmtNum(v / 1e6, 0));
const isFail = (s: string) => /fail|error|dead/i.test(s);
const stamp = (t: number | undefined) => (t != null ? new Date(t).toISOString() : undefined);

function ArtifactLink({ r }: { r: JobRow }) {
  const p = pageOf(r.kind);
  if (!r.artifact_id) return <span className="num">—</span>;
  if (!p) return <span className="num">{r.artifact_id.slice(-6)}</span>;
  return (
    <Link to={p.to} className="num cp-art" aria-label={`${r.kind} artifact ${r.artifact_id} on ${p.code}`}>
      {p.code} →
    </Link>
  );
}

function Progress({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="num">—</span>;
  const f = Math.max(0, Math.min(1, v));
  return (
    <span className="cp-prog">
      <span className="cp-prog-bar" aria-hidden>
        <span className="cp-prog-fill" style={{ "--w": `${f * 100}%` } as CSSProperties} />
      </span>
      <span className="num">{fmtPct(f, 0)}</span>
    </span>
  );
}

const kindCell = (r: JobRow) => <span className="num">{r.kind}</span>;

const RUN_COLS: Column<JobRow>[] = [
  { key: "kind", label: "KIND", render: kindCell },
  { key: "progress", label: "PROG", width: "34%", render: (r) => <Progress v={r.progress} /> },
  { key: "message", label: "STEP", hideBelow: 900, render: (r) => <span className="num">{r.message ?? "—"}</span> },
  { key: "started_at", label: "START", align: "right", render: (r) => <span className="num">{fmtStamp(r.started_at ?? null)}</span> },
];

const DONE_COLS: Column<JobRow>[] = [
  { key: "kind", label: "KIND", render: kindCell },
  { key: "state", label: "STATE", render: (r) => <span className={isFail(r.state) ? "num loss" : "num"}>{r.state.toUpperCase()}</span> },
  { key: "finished_at", label: "END", align: "right", render: (r) => <span className="num">{fmtStamp(r.finished_at ?? null)}</span> },
  { key: "cpu_seconds", label: "CPU S", numeric: true, format: (v) => fmtNum(v, 1) },
  { key: "peak_rss_bytes", label: "RSS MB", numeric: true, hideBelow: 600, format: (v) => mb(v) },
  { key: "submitted_by", label: "BY", hideBelow: 900, render: (r) => <span className="num">{(r.submitted_by ?? "—").toUpperCase()}</span> },
  { key: "artifact_id", label: "ART", align: "right", render: (r) => <ArtifactLink r={r} /> },
];

const SLICE_COLS: Column<SliceRow>[] = [
  { key: "name", label: "SLICE", render: (r) => <span className="num">{r.name}</span> },
  { key: "cpu", label: "CPU CORES", numeric: true, format: (v) => fmtNum(v, 2) },
  { key: "mem", label: "MEM GB", numeric: true, format: (v) => (v == null ? "—" : fmtNum(v / 1e9, 2)) },
];

const KERNEL_COLS: Column<KernelRow>[] = [
  {
    key: "name",
    label: "KERNEL",
    render: (r) => (
      <a className="num cp-kernel" href={`/methodology#kernel-${r.name}`}>
        {r.name}
      </a>
    ),
  },
  { key: "engine", label: "HERE", hideBelow: 600, render: (r) => <span className="num">{r.engine ? r.engine.toUpperCase() : "—"}</span> },
  { key: "python_ms", label: "PY MS", numeric: true, format: (v) => fmtNum(v, 2) },
  { key: "rust_1t_ms", label: "RS 1T MS", numeric: true, format: (v) => fmtNum(v, 2) },
  { key: "rust_2t_ms", label: "RS 2T MS", numeric: true, hideBelow: 600, format: (v) => (v == null ? "1T ONLY" : fmtNum(v, 2)) },
  { key: "speedup", label: "1T ×", numeric: true, format: (v) => fmtMultiple(v, 1) },
  { key: "scaling", label: "2T/1T", numeric: true, hideBelow: 900, format: (v) => (v == null ? "—" : fmtMultiple(v, 2)) },
  { key: "sha", label: "SHA", align: "right", hideBelow: 1200, render: (r) => <span className="num">{r.sha ? r.sha.slice(0, 7) : "—"}</span> },
];

/** A list read's rows, merged with the job stream. */
function useLiveJobs(data: JobsOut | undefined): { rows: JobRow[]; link: StreamLink } {
  const [rows, setRows] = useState<JobRow[]>([]);
  const ref = useRef<JobRow[]>([]);
  useEffect(() => {
    ref.current = Array.isArray(data?.jobs) ? data.jobs : [];
    setRows(ref.current);
  }, [data]);
  const onEvent = useCallback((e: JobEvent) => {
    const out = applyEvent(ref.current, e);
    ref.current = out.rows;
    setRows(out.rows);
    if (out.refetch) {
      void queryClient.invalidateQueries({ queryKey: ["GET", "/jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["GET", "/ops"] });
    }
  }, []);
  const link = useJobEvents(onEvent);
  return { rows, link };
}

interface ScheduleRow {
  name: string;
  cron?: string;
  next_run?: string | null;
}

const SCHED_COLS: Column<ScheduleRow>[] = [
  { key: "name", label: "NAME", render: (r) => <span className="num">{r.name}</span> },
  { key: "cron", label: "CRON NY", hideBelow: 600, render: (r) => <span className="num">{r.cron ?? "—"}</span> },
  { key: "next_run", label: "NEXT", align: "right", render: (r) => <span className="num">{fmtStamp(r.next_run ?? null)}</span> },
];

/** The schedule table: a 404 here means the route is not served (schedules exist), not "not yet run". */
function ScheduleCell() {
  const q = useApiQuery<unknown>("/jobs/schedules", undefined, { staleTime: 10 * 60_000 });
  if (q.isError) {
    const notServed = q.error instanceof ApiError && q.error.status === 404;
    return (
      <Panel title="SCHEDULE">
        <Absent reason={notServed ? "NOT SERVED" : "DATA UNAVAILABLE"} source="GET /api/jobs/schedules · schedules.yaml" />
      </Panel>
    );
  }
  return (
    <ReadCell<unknown> title="SCHEDULE" query={q} source="GET /api/jobs/schedules" flush skeletonHeight={120}>
      {(d) => {
        const list = (Array.isArray(d) ? d : ((d as { schedules?: unknown[] } | null)?.schedules ?? [])) as ScheduleRow[];
        return <DataTable<ScheduleRow> compact columns={SCHED_COLS} rows={list} rowKey={(r) => r.name} empty="NO SCHEDULES" />;
      }}
    </ReadCell>
  );
}

function KernelDispatch({ query }: { query: ReturnType<typeof useApiQuery<unknown>> }) {
  return (
    <ReadCell<unknown> title="KERNEL DISPATCH" query={query} source="GET /api/ops/kernels" skeletonHeight={96} notes={[]}>
      {(d) => {
        const b = (d ?? {}) as { engine?: string; engines?: Record<string, string> };
        const engines = Object.values(b.engines ?? {});
        return (
          <dl className="oc-kv">
            <div className="oc-kv-row">
              <dt>PROCESS</dt>
              <dd className="num">{b.engine ? b.engine.toUpperCase() : "—"}</dd>
            </div>
            <div className="oc-kv-row">
              <dt>ON RUST</dt>
              <dd className="num">
                {fmtNum(engines.filter((e) => e === "rust").length, 0)} / {fmtNum(engines.length, 0)}
              </dd>
            </div>
          </dl>
        );
      }}
    </ReadCell>
  );
}

export default function Compute() {
  const [params] = useSearchParams();
  const kind = params.get("kind")?.toLowerCase() || undefined;
  const host = useApiQuery<HostFull>("/ops/host", undefined, { staleTime: 5_000, refetchInterval: (q) => (q.state.status === "error" ? 5 * 60_000 : 10_000) });
  const ops = useOps();
  const jobs = useApiQuery<JobsOut>("/jobs", { limit: 200, kind }, { staleTime: 15_000, refetchInterval: (q) => (q.state.status === "error" ? 5 * 60_000 : 60_000) });
  const kernels = useApiQuery<unknown>("/ops/kernels", undefined, { staleTime: 30 * 60_000 });
  const { rows, link } = useLiveJobs(jobs.data);

  const series = useMemo(() => hostSeries(host.data), [host.data]);
  const running = useMemo(() => rows.filter((r) => r.state === "running"), [rows]);
  const recent = useMemo(() => last24h(rows, Date.now()), [rows]);
  const hostAsOf = stamp(host.data?.latest?.t_ms);
  const jobsSrc = `GET /api/jobs?limit=200${kind ? `&kind=${kind}` : ""}`;
  const hist = { query: host, source: "GET /api/ops/host · history", asOf: hostAsOf, maxAgeSec: 60, skeletonHeight: 180, notes: [] as string[], provenance: [] };
  const noHist = <Absent reason="NO HISTORY" source="hostd /v1/host · history" />;

  return (
    <Page
      title="Compute"
      meta={
        <>
          {host.data?.cpus != null && <span>{fmtNum(host.data.cpus, 0)} VCPU</span>}
          <span className="cp-link">
            <Lamp state={link === "LIVE" ? "ok" : link === "DOWN" ? "fault" : "stale"} label="Job stream" /> STREAM {link}
          </span>
          {kind && (
            <span>
              KIND {kind.toUpperCase()} · <Link to="/compute">ALL</Link>
            </span>
          )}
        </>
      }
    >
      <ReadCell<HostFull> title="HOST · NOW" query={host} source="GET /api/ops/host" asOf={hostAsOf} maxAgeSec={60} skeletonHeight={96}>
        {(d) => {
          const l = d.latest;
          if (!l) return <Absent reason="FIRST SAMPLE PENDING" source="hostd /v1/host · latest" />;
          const used = l.mem_total != null && l.mem_available != null ? l.mem_total - l.mem_available : null;
          return (
            <StatGrid min={130}>
              <StatTile size="md" label="CPU" value={fmtPct(l.cpu, 0)} caption={d.cpus ? `OF ${fmtNum(d.cpus, 0)} VCPU` : undefined} />
              <StatTile size="md" label="STEAL" value={fmtPct(l.steal, 1)} tone={(l.steal ?? 0) > 0.05 ? "loss" : "neutral"} />
              <StatTile size="md" label="IOWAIT" value={fmtPct(l.iowait, 1)} />
              <StatTile size="md" label="MEM USED" value={gb(used)} caption={l.mem_total != null ? `OF ${gb(l.mem_total)}` : undefined} />
              <StatTile size="md" label="SWAP" value={gb(l.swap_used)} tone={(l.swap_used ?? 0) > 0 ? "loss" : "neutral"} />
              <StatTile size="md" label="LOAD 1M" value={fmtNum(l.load1, 2)} />
            </StatGrid>
          );
        }}
      </ReadCell>

      {!host.isError && (
        <div className="grid-3 cp-charts">
          <ReadCell<HostFull> title="CPU · CORES · 1H" {...hist}>
            {() =>
              series ? (
                <XYChart
                  x={series.minAgo}
                  xTicks={HOUR_TICKS} xTitle="MIN" xFormat="int"
                  series={[{ name: "TOTAL", y: series.cpuCores, tone: "ink" }, ...series.slices.map((s) => ({ name: s.name, y: s.y, tone: "ink3" as const, span: true }))]}
                  yFormat="num"
                  digits={2}
                  zero
                  height={180}
                  ariaLabel="Host CPU in cores over the last hour, total and per slice"
                />
              ) : (
                noHist
              )
            }
          </ReadCell>
          <ReadCell<HostFull> title="STEAL · 1H" {...hist}>
            {() =>
              series ? (
                <XYChart
                  x={series.minAgo}
                  xTicks={HOUR_TICKS} xTitle="MIN" xFormat="int"
                  series={[{ name: "STEAL", y: series.steal, tone: "ink" }]}
                  hlines={[{ at: 0.05, label: "5%", tone: "signal", dash: "dot" }]}
                  yFormat="pct"
                  digits={1}
                  zero
                  height={180}
                  ariaLabel="CPU steal over the last hour"
                />
              ) : (
                noHist
              )
            }
          </ReadCell>
          <ReadCell<HostFull> title="MEM USED · GB · 1H" {...hist}>
            {(d) =>
              series ? (
                <XYChart
                  x={series.minAgo}
                  xTicks={HOUR_TICKS} xTitle="MIN" xFormat="int"
                  series={[{ name: "USED", y: series.memUsedGb, tone: "ink" }]}
                  hlines={d.latest?.mem_total != null ? [{ at: d.latest.mem_total / 1e9, label: "TOTAL", tone: "ink3", dash: "dot" }] : undefined}
                  yFormat="num"
                  digits={2}
                  zero
                  height={180}
                  ariaLabel="Host memory used in gigabytes over the last hour"
                />
              ) : (
                noHist
              )
            }
          </ReadCell>
        </div>
      )}

      <div className="grid-2">
        <ReadCell<OpsOut> title="QUEUE · 24H" query={ops} source="GET /api/ops" skeletonHeight={96}>
          {(d) => {
            const j = d.jobs;
            if (!j) return <Absent reason="NOT YET RUN" source="GET /api/ops · jobs" />;
            return (
              <StatGrid min={96}>
                <StatTile size="sm" label="QUEUED" value={fmtNum(j.queued, 0)} />
                <StatTile size="sm" label="RUNNING" value={fmtNum(j.running, 0)} />
                <StatTile size="sm" label="DONE" value={fmtNum(j.done_24h, 0)} />
                <StatTile size="sm" label="FAILED" value={fmtNum(j.failed_24h, 0)} tone={(j.failed_24h ?? 0) > 0 ? "loss" : "neutral"} />
                <StatTile size="sm" label="CORE-H" value={j.cpu_seconds_24h != null ? fmtNum(j.cpu_seconds_24h / 3600, 2) : null} />
              </StatGrid>
            );
          }}
        </ReadCell>
        {host.isError ? (
          <KernelDispatch query={kernels} />
        ) : (
          <ReadCell<HostFull> title="SLICES · NOW" query={host} source="GET /api/ops/host · groups" asOf={hostAsOf} maxAgeSec={60} flush skeletonHeight={96}>
            {(d) => {
              const list: SliceRow[] = Object.entries(d.latest?.groups ?? {}).map(([k, g]) => ({ name: sliceLabel(k), cpu: g?.cpu ?? null, mem: g?.mem ?? null }));
              return <DataTable<SliceRow> compact columns={SLICE_COLS} rows={list} rowKey={(r) => r.name} empty="NO SLICES" />;
            }}
          </ReadCell>
        )}
      </div>

      <ReadCell<JobsOut> title={`RUNNING · ${fmtNum(running.length, 0)}`} query={jobs} source={jobsSrc} flush skeletonHeight={96}>
        {() => <DataTable<JobRow> compact columns={RUN_COLS} rows={running} rowKey={(r) => r.id} scrollLabel="Running jobs" empty="NONE RUNNING" />}
      </ReadCell>

      <ReadCell<JobsOut> title={`LAST 24H · ${fmtNum(recent.length, 0)}`} query={jobs} source={jobsSrc} flush skeletonHeight={160}>
        {() => <DataTable<JobRow> compact columns={DONE_COLS} rows={recent} rowKey={(r) => r.id} scrollLabel="Jobs finished in the last 24 hours" empty="NO JOBS FINISHED IN 24H" />}
      </ReadCell>

      <div className="grid-2">
        <ScheduleCell />
        {!host.isError && <KernelDispatch query={kernels} />}
      </div>

      <ReadCell<unknown> title="KERNELS · RUST VS PYTHON · MEDIAN MS" query={kernels} source="GET /api/ops/kernels" flush skeletonHeight={200}>
        {(d) => {
          const list = kernelRows(d);
          const where = measuredOn(list);
          return (
            <>
              {where.length > 0 && <div className="cp-measured num">MEASURED ON {where.join(" · ").toUpperCase()}</div>}
              <DataTable<KernelRow> compact columns={KERNEL_COLS} rows={list} rowKey={(r) => r.name} empty="NO BENCHMARK RUN" scrollLabel="Kernel benchmarks" />
            </>
          );
        }}
      </ReadCell>
    </Page>
  );
}
