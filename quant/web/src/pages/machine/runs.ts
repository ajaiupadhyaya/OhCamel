/**
 * The day's runs, kept live from the job stream. Pure: no clock, no network, no React.
 *
 * `GET /api/machine` carries the runs as they were when it answered; `GET /api/jobs/events` then says how
 * each job moves. An event is the job's newest reading (state, progress and message together), so it replaces
 * those three fields of the run it names, except that its message is kept only when it is progress text (below).
 * It carries no times or cpu: a run that has just started or finished keeps its `start`, `end` and `cpu`
 * until the next read of the machine, which an unseen id asks for.
 */
import type { JobEvent, Run, RunState } from "./types";

const STATES: readonly RunState[] = ["queued", "running", "done", "failed", "cancelled"];
/** The jobs database never moves a job out of these (a retry goes running to queued, never done to anything). */
const FINAL: readonly RunState[] = ["done", "failed", "cancelled"];
/**
 * Controller ruling 16. Progress text arrives only with a `running` event. A `failed` event carries the job's
 * error, a retry's `queued` event "retrying after: <error>", and the other `queued` events free text (queue.py),
 * and `/api/machine` never carries any of that (load_runs: "a run carries neither its error nor its params").
 * So a run read from these events has no msg.
 */
const NO_TEXT: readonly RunState[] = ["queued", "failed", "cancelled"];

/**
 * `runs` with the event applied. An id the runs do not hold comes back `unknown: true` with the runs
 * unchanged, which is the caller's cue to read the machine again. A run that has finished is final: an
 * event for it changes nothing, so a late or repeated one cannot undo it. When nothing changes, the very
 * same array comes back, so a duplicate event costs no render.
 */
export function applyJobEvent(runs: Run[], e: JobEvent): { runs: Run[]; unknown: boolean } {
  const i = runs.findIndex((r) => r.id === e.id);
  if (i < 0) return { runs, unknown: true };
  const cur = runs[i];
  if (FINAL.includes(cur.state)) return { runs, unknown: false };
  const msg = NO_TEXT.includes(e.state) ? null : e.message;
  if (cur.state === e.state && cur.progress === e.progress && cur.msg === msg) return { runs, unknown: false };
  const next = runs.slice();
  next[i] = { ...cur, state: e.state, progress: e.progress, msg };
  return { runs: next, unknown: false };
}

/** One `event: job` data line, or null when it is not one. A progress or message it cannot trust is null. */
export function parseJobEvent(data: string): JobEvent | null {
  let v: unknown;
  try {
    v = JSON.parse(data);
  } catch {
    return null;
  }
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  const state = STATES.find((s) => s === o.state);
  if (typeof o.id !== "string" || !o.id || !state) return null;
  return {
    type: "job",
    id: o.id,
    state,
    progress: typeof o.progress === "number" && Number.isFinite(o.progress) ? o.progress : null,
    message: typeof o.message === "string" ? o.message : null,
  };
}
