/**
 * The lamp panel's compute bank (spec §3.7): what the box is doing while the deck flies.
 * One lamp for hostd, three bar lamps (CPU, steal, memory) from GET /api/ops/host, and one
 * lamp per running job kind from GET /api/jobs?state=running, kept current by the job stream
 * (GET /api/jobs/events, F3: live.ts, useJobStream.ts). The reading is compute.ts; this file
 * only draws it. A read that fails is drawn as failed, never as idle.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ApiError } from "../../lib/api";
import { fmtPct } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import { computeBank, type HostState, type HostWire, type JobRow, type JobsWire } from "./compute";
import { applyJobEvent, jobsPollInterval, type JobEvent, type LinkState } from "./live";
import { useJobStream } from "./useJobStream";

const POLL = 15_000;
const POLL_ERR = 5 * 60_000;

const LINK_WORD: Record<LinkState, string> = { live: "SSE", connecting: "SSE …", down: "POLL 15 s" };

const HOST_WORD: Record<HostState, string> = { ok: "LIVE", stale: "STALE", off: "NOT SET", down: "DOWN", warming: "WARMING", unknown: "—" };
const HOST_TONE: Record<HostState, string> = { ok: "ok", stale: "stale", off: "off", down: "down", warming: "stale", unknown: "off" };

function configuredOf(e: ApiError | null): { configured: boolean } | null {
  if (!e) return null;
  const body = e.body as { configured?: boolean } | undefined;
  return { configured: body?.configured !== false };
}

/**
 * Running jobs: the list read seeds them; each stream event then moves a progress or drops a
 * job without a read; a job the deck has not seen starting asks for one list read (its kind is
 * only on the list). While the stream is down the list is polled every 15 s, as before F3.
 */
function useRunningJobs() {
  const [held, setHeld] = useState<JobRow[] | null>(null);
  const heldRef = useRef<JobRow[] | null>(null);
  const refetchRef = useRef<() => unknown>(() => undefined);
  const onEvent = useCallback((e: JobEvent) => {
    const h = heldRef.current;
    if (!h) return;
    const r = applyJobEvent(h, e);
    if (r.jobs !== h) {
      heldRef.current = r.jobs;
      setHeld(r.jobs);
    }
    if (r.needsList) void refetchRef.current();
  }, []);
  const link = useJobStream(onEvent);
  const jobs = useApiQuery<JobsWire>("/jobs", { state: "running", limit: 50 }, { staleTime: 0, refetchInterval: (q) => jobsPollInterval(link, q.state.status === "error") });
  const { data, dataUpdatedAt, refetch } = jobs;
  refetchRef.current = refetch;
  // a new list read replaces whatever the stream had moved
  useEffect(() => {
    if (!data) return;
    heldRef.current = data.jobs ?? [];
    setHeld(heldRef.current);
  }, [data, dataUpdatedAt]);
  // the stream (re)opened: events may have been missed while it was down, so read the list once
  useEffect(() => {
    if (link === "live") void refetch();
  }, [link, refetch]);
  const live: JobsWire | undefined = data ? { ...data, jobs: held ?? data.jobs } : undefined;
  return { jobs, live, link };
}

export function ComputeBank({ onInspect }: { onInspect: (text: string) => void }) {
  const host = useApiQuery<HostWire>("/ops/host", undefined, { staleTime: 0, refetchInterval: (q) => (q.state.status === "error" ? POLL_ERR : POLL) });
  const { jobs, live, link } = useRunningJobs();
  // React Query keeps `data` after a later fetch fails (keepPreviousData): an error with a held
  // reading is "stale", drawn as old, never as live; an error with nothing held is down/off.
  const hostConfigured = host.data ? null : (configuredOf(host.error ?? null)?.configured ?? null);
  const hostStale = host.isError && !!host.data;
  const jobsStale = jobs.isError && !!jobs.data;
  const bank = useMemo(
    () =>
      computeBank({
        host: host.data ?? null,
        hostError: hostConfigured == null ? null : { configured: hostConfigured },
        hostStale,
        jobs: live ?? null,
        jobsError: !jobs.data && jobs.isError,
        jobsStale,
      }),
    [host.data, hostConfigured, hostStale, live, jobs.data, jobs.isError, jobsStale],
  );
  const cpus = host.data?.cpus;

  return (
    <div className="dk-cmp" role="group" aria-label="Compute bank">
      <ul className="dk-cmp-bars" aria-hidden="true">
        {bank.bars.map((b) => (
          <li key={b.key} className={`dk-cmp-bar ${b.level}${b.stale ? " stale" : ""}`}>
            <span className="dk-cmp-label">{b.label}</span>
            <span className="dk-cmp-track">
              <span className="dk-cmp-fill" style={{ width: `${b.fill * 100}%` }} />
              <span className="dk-cmp-tick" style={{ left: "75%" }} />
              <span className="dk-cmp-tick over" style={{ left: "90%" }} />
            </span>
            <span className="dk-cmp-value num">{b.text}</span>
          </li>
        ))}
      </ul>
      <ul className="dk-lampfield" aria-label="Compute lamps">
        <li className={`dk-key ${HOST_TONE[bank.host]}`}>
          <button
            type="button"
            className="dk-key-face"
            onClick={() =>
              onInspect(
                `HOSTD · ${HOST_WORD[bank.host]}${bank.host === "ok" && cpus ? ` · ${cpus} CPU` : ""}${bank.host === "stale" ? (hostStale ? " · last refresh failed, bars show the previous reading" : " · hostd sampler has not reported recently, bars show its last sample") : ""}${host.error ? ` · ${host.error.detail}` : ""}${host.data?.latest?.swap_used != null ? ` · swap ${fmtPct((host.data.latest.swap_used ?? 0) / Math.max(1, host.data.latest.mem_total ?? 1), 1)} of memory` : ""}`,
              )
            }
          >
            <span className="dk-key-legend">HOSTD</span>
            <span className="dk-key-state">{HOST_WORD[bank.host]}</span>
          </button>
        </li>
        <li className={`dk-key ${link === "live" ? "ok" : link === "connecting" ? "stale" : "off"}`}>
          <button
            type="button"
            className="dk-key-face"
            onClick={() =>
              onInspect(
                link === "live"
                  ? "JOB STREAM · SSE · GET /api/jobs/events open; job progress and finishes arrive as they happen, the running list is re-read every 2 min to reconcile"
                  : link === "connecting"
                    ? "JOB STREAM · CONNECTING · opening GET /api/jobs/events; the running list is read every 15 s meanwhile"
                    : "JOB STREAM · DOWN · GET /api/jobs/events is not answering; retrying with backoff, the running list is read every 15 s meanwhile",
              )
            }
          >
            <span className="dk-key-legend">JOB LINK</span>
            <span className="dk-key-state">{LINK_WORD[link]}</span>
          </button>
        </li>
        {bank.jobs === "down" || bank.jobs === "unknown" ? (
          <li className={`dk-key ${bank.jobs === "down" ? "down" : "off"}`}>
            <button type="button" className="dk-key-face" onClick={() => onInspect(`JOBS · ${bank.jobs === "down" ? `read failed${jobs.error ? ` · ${jobs.error.detail}` : ""}` : "not read yet"}`)}>
              <span className="dk-key-legend">JOBS</span>
              <span className="dk-key-state">{bank.jobs === "down" ? "DOWN" : "—"}</span>
            </button>
          </li>
        ) : bank.lamps.length === 0 ? (
          bank.jobs === "stale" ? (
            <li className="dk-key stale">
              <button type="button" className="dk-key-face" onClick={() => onInspect(`JOBS · STALE · last refresh failed${jobs.error ? ` · ${jobs.error.detail}` : ""} · the previous read showed no jobs running`)}>
                <span className="dk-key-legend">JOBS</span>
                <span className="dk-key-state">STALE</span>
              </button>
            </li>
          ) : (
            <li className="dk-key off">
              <span className="dk-key-face">
                <span className="dk-key-legend">JOBS</span>
                <span className="dk-key-state">IDLE</span>
              </span>
            </li>
          )
        ) : (
          bank.lamps.map((l) => (
            <li key={l.kind} className={`dk-key ${bank.jobs === "stale" ? "stale" : "job"}`}>
              <button
                type="button"
                className="dk-key-face"
                title={l.kind}
                onClick={() =>
                  onInspect(
                    `${l.kind} · ${l.count} running${l.progress != null ? ` · least advanced ${fmtPct(l.progress, 0)}` : ""}${bank.jobs === "stale" ? ` · STALE: last refresh failed${jobs.error ? ` (${jobs.error.detail})` : ""}, this is the previous read` : ""}`,
                  )
                }
              >
                <span className="dk-key-legend">{l.legend}</span>
                <span className="dk-key-state num">
                  ×{l.count}
                  {l.progress != null ? ` · ${fmtPct(l.progress, 0)}` : ""}
                  {bank.jobs === "stale" ? " · OLD" : ""}
                </span>
              </button>
            </li>
          ))
        )}
      </ul>
      <p className="sr-only">{bank.summary}</p>
    </div>
  );
}
