/**
 * The Machine's data: `GET /api/machine`, with its runs kept live from the job stream
 * (`GET /api/jobs/events`, one EventSource, closed on unmount).
 *
 * The graph is cached on the server for five minutes, so the page reads it every five. The stream is a
 * nicety on top: while it is up, each job event patches the run it names (runs.ts), and an event for a job
 * the page has not seen asks for a fresh read, at most once in 30 seconds, and once for each job: a job the
 * read does not bring is one the page does not draw (a visitor's `api.*`), and is not asked about again.
 * The server ends every stream at its lifetime or idle limit; the browser reconnects by itself, and that is
 * left alone. A tab that is hidden holds no stream, since the server has only so many, and opens one when it is
 * shown. When the stream fails hard it is closed and tried again after five minutes. Meanwhile the page polls
 * every minute when it has no other way to learn of runs, but stays at five when the server refused the stream,
 * as it does at capacity: a refusal means the server is at its busiest, and that is no moment to read it more.
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

/** How the job stream stands: up, unreachable or absent (`down`), or turned away by the server (`refused`). */
export type StreamState = "up" | "down" | "refused";

/** How often to read the graph with the stream in this state. */
export const pollMs = (stream: StreamState): number => (stream === "down" ? POLL_DOWN_MS : POLL_MS);

/** The part of `document` the stream watches: a test hands in its own. */
export type Visibility = Pick<Document, "hidden" | "addEventListener" | "removeEventListener">;

/**
 * Opens the job stream and reports it: `job` for each event it can read, `up` when it opens, `down` when it
 * fails hard, with whether the server refused it (the stream is then closed, and opened again after `RETRY_MS`).
 * Returns the function that stops it. Without an EventSource it is down from the start and has nothing to stop.
 *
 * The server ends each stream at its lifetime (300 s) or idle limit (120 s). The browser reports that as an
 * error with readyState CONNECTING and reconnects by itself with Last-Event-ID, as in the Compute page's and
 * the Deck's hooks, so an error on a stream that had opened is left alone. A hard failure is readyState CLOSED
 * (the browser gives up on any answer that is not an event stream, a 503 at capacity among them: EventSource
 * shows no status, so this is the refusal), or an error before any open: the first connection failing, or the
 * browser's reconnection failing, which says the network is down and not that the server is busy.
 *
 * While `page` is hidden there is no stream. That is not a failure: nothing is reported, and showing the page
 * opens a new one, unless a hard failure is still waiting out its `RETRY_MS`. A new stream starts from now, so
 * `back` says the page was away and may have missed events.
 */
export function watchJobs(
  on: { job: (e: JobEvent) => void; up: () => void; down: (refused: boolean) => void; back: () => void },
  page: Visibility | undefined = typeof document === "undefined" ? undefined : document,
): () => void {
  if (typeof EventSource === "undefined") {
    on.down(false);
    return () => {};
  }
  let source: EventSource | null = null;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let away = false;
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
      const refused = s.readyState === EventSource.CLOSED;
      const ended = opened && !refused;
      opened = false;
      if (ended) return;
      s.close();
      source = null;
      on.down(refused);
      retry = setTimeout(() => {
        retry = undefined;
        follow();
      }, RETRY_MS);
    };
  };
  /** Has a stream exactly when the page is shown and no retry is pending. */
  const follow = () => {
    if (page?.hidden) {
      away = true;
      source?.close();
      source = null;
      return;
    }
    if (source === null && retry === undefined) open();
    if (away) on.back();
    away = false;
  };
  follow();
  page?.addEventListener("visibilitychange", follow);
  return () => {
    page?.removeEventListener("visibilitychange", follow);
    clearTimeout(retry);
    source?.close();
    source = null;
  };
}

/** The most ids `unseenJobs` remembers; past it, it starts again, and each job costs one more read at most. */
const MAX_UNSEEN = 1000;

/**
 * The jobs the page has heard of through the stream and that a read did not bring. `ask` says whether an unknown
 * job is worth a read, and `read` is told what each read brought. A job still unknown after a read is remembered and
 * never asked about again: it is not one of the clock's (a visitor's `api.*`), and would re-arm a read every 30 s.
 */
export function unseenJobs(): { ask(id: string): boolean; read(runs: Run[]): void } {
  let waiting = new Set<string>();
  const gone = new Set<string>();
  return {
    ask(id) {
      if (gone.has(id)) return false;
      waiting.add(id);
      return true;
    },
    read(runs) {
      const brought = new Set(runs.map((r) => r.id));
      if (gone.size + waiting.size > MAX_UNSEEN) gone.clear();
      waiting.forEach((id) => !brought.has(id) && gone.add(id));
      waiting = new Set();
    },
  };
}

export function useMachine(): { data: MachineOut | undefined; error: unknown; runs: Run[]; retry: () => void } {
  const [stream, setStream] = useState<StreamState>(typeof EventSource === "undefined" ? "down" : "up");
  const { data, dataUpdatedAt, error, refetch } = useApiQuery<MachineOut>("/machine", undefined, {
    staleTime: 60_000,
    refetchInterval: pollMs(stream),
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

  // By the read's time, not its runs: a read that brings the same runs is still an answer, and must settle the ids.
  const [unseen] = useState(unseenJobs);
  useEffect(() => {
    if (dataUpdatedAt > 0) unseen.read(held.current.served);
  }, [dataUpdatedAt, unseen]);

  useEffect(
    () =>
      watchJobs({
        job: (e) => {
          const cur = held.current;
          const out = applyJobEvent(cur.runs, e);
          if (out.unknown) {
            if (unseen.ask(e.id)) refetchSoon();
          } else if (out.runs !== cur.runs) {
            held.current = { served: cur.served, runs: out.runs };
            setPatched({ from: cur.served, runs: out.runs });
          }
        },
        up: () => setStream("up"),
        down: (refused) => setStream(refused ? "refused" : "down"),
        back: refetchSoon,
      }),
    [refetchSoon, unseen],
  );

  return { data, error, runs, retry: () => void refetch() };
}
