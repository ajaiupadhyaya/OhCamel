/**
 * The Machine's data: `GET /api/machine`, with its runs kept live from the job stream
 * (`GET /api/jobs/events`, one EventSource, closed on unmount).
 *
 * The graph is cached on the server for five minutes, so the page reads it every five. The stream is a
 * nicety on top: while it is up, each job event patches the run it names (runs.ts), and an event for a job
 * the page has not seen asks for a fresh read, at most once in 30 seconds. The server ends every stream at
 * its lifetime or idle limit; the browser reconnects by itself, and that is left alone. When the stream fails
 * hard it is closed, the page polls every minute instead, and the stream is tried again after five.
 */
import { useEffect, useRef, useState } from "react";
import { useApiQuery } from "../../lib/query";
import { applyJobEvent, parseJobEvent } from "./runs";
import type { JobEvent, MachineOut, Run } from "./types";

export const POLL_MS = 300_000;
export const POLL_DOWN_MS = 60_000;
export const RETRY_MS = 300_000;
export const REFETCH_GAP_MS = 30_000;

const STREAM = "/api/jobs/events";
const NO_RUNS: Run[] = [];

/** `fn`, but a call within `gap` ms of the last one it let through does nothing. */
export function atMostEvery(gap: number, fn: () => void, now: () => number = Date.now): () => void {
  let last = -Infinity;
  return () => {
    const t = now();
    if (t - last < gap) return;
    last = t;
    fn();
  };
}

/**
 * Opens the job stream and reports it: `job` for each event it can read, `up` when it opens, `down` when it
 * fails hard (the stream is then closed, and opened again after `RETRY_MS`). Returns the function that stops
 * it. Without an EventSource it is down from the start and has nothing to stop.
 *
 * The server ends each stream at its lifetime (300 s) or idle limit (120 s). The browser reports that as an
 * error with readyState CONNECTING and reconnects by itself with Last-Event-ID, as in the Compute page's and
 * the Deck's hooks, so an error on a stream that had opened is left alone. A hard failure is readyState CLOSED
 * (a refused connection, a response that is not an event stream), or an error before any open: the first
 * connection failing, or the browser's reconnection failing.
 */
export function watchJobs(on: { job: (e: JobEvent) => void; up: () => void; down: () => void }): () => void {
  if (typeof EventSource === "undefined") {
    on.down();
    return () => {};
  }
  let source: EventSource | null = null;
  let retry: ReturnType<typeof setTimeout> | undefined;
  const open = () => {
    const s = new EventSource(STREAM);
    let opened = false; // since the last error
    source = s;
    s.onopen = () => {
      opened = true;
      on.up();
    };
    s.addEventListener("job", (m) => {
      const e = parseJobEvent((m as MessageEvent<string>).data);
      if (e) on.job(e);
    });
    s.onerror = () => {
      const ended = opened && s.readyState !== EventSource.CLOSED;
      opened = false;
      if (ended) return;
      s.close();
      source = null;
      on.down();
      retry = setTimeout(open, RETRY_MS);
    };
  };
  open();
  return () => {
    clearTimeout(retry);
    source?.close();
    source = null;
  };
}

export function useMachine(): { data: MachineOut | undefined; error: unknown; runs: Run[] } {
  const [down, setDown] = useState(typeof EventSource === "undefined");
  const { data, error, refetch } = useApiQuery<MachineOut>("/machine", undefined, {
    staleTime: 60_000,
    refetchInterval: down ? POLL_DOWN_MS : POLL_MS,
  });

  // The runs the server last sent, with the stream's events applied on top. A read that brings different
  // runs replaces the patched ones.
  const served = data?.runs ?? NO_RUNS;
  const [patched, setPatched] = useState<{ from: Run[]; runs: Run[] } | null>(null);
  const runs = patched?.from === served ? patched.runs : served;
  const held = useRef({ served, runs }); // what the stream's handler sees, kept ahead of the render
  held.current = { served, runs };

  // Held in state, not memoised: React may discard a memo, and a new throttle would forget the last read.
  const latestRefetch = useRef(refetch);
  latestRefetch.current = refetch;
  const [refetchSoon] = useState(() => atMostEvery(REFETCH_GAP_MS, () => void latestRefetch.current({ cancelRefetch: false })));

  useEffect(
    () =>
      watchJobs({
        job: (e) => {
          const cur = held.current;
          const out = applyJobEvent(cur.runs, e);
          if (out.unknown) refetchSoon();
          else if (out.runs !== cur.runs) {
            held.current = { served: cur.served, runs: out.runs };
            setPatched({ from: cur.served, runs: out.runs });
          }
        },
        up: () => setDown(false),
        down: () => setDown(true),
      }),
    [refetchSoon],
  );

  return { data, error, runs };
}
