/**
 * The Machine's day: the runs of the last 24 hours, and the replay that sweeps a hand across them. Pure, no
 * clock and no DOM; `now` comes from the caller.
 *
 * Which runs are in the day is the readings' rule (`ranInLastDay` in prose.ts), so the Day tab and a job's
 * reading never disagree. Everything keys on `state`, never on `start` (ruling 17): a queued run has no start,
 * and a run that has only just started may have none until the next read of the machine.
 */
import { instant } from "./fmt";
import { DAY_MS, fin, ranInLastDay } from "./prose";
import type { Run } from "./types";

/** How long the replay takes to sweep the day, in seconds. */
export const REPLAY_S = 26;

/** When a run is dated: its start, else its end. A run with neither (or one that does not read) is dated Infinity, as new as can be. */
const dated = (r: Run): number => instant(r.start ?? r.end) ?? Infinity;

/** Newest first: by date, then by id, the later first (ids are ULIDs, so they run in time), so the order never varies. */
function newestFirst(a: Run, b: Run): number {
  const ta = dated(a), tb = dated(b);
  if (ta !== tb) return ta < tb ? 1 : -1;
  return a.id < b.id ? 1 : a.id > b.id ? -1 : 0;
}

/**
 * The runs of the day, in the order the Day tab lists them: those running now, then those queued, then the
 * finished runs of the last 24 hours (`ranInLastDay`), each group newest first. A running or queued run is
 * listed whatever its start, even one that began more than a day ago; one with no start yet counts as the newest.
 * The runs themselves come back, in a new array.
 */
export function dayRuns(runs: Run[], now: Date): Run[] {
  const group = (keep: (r: Run) => boolean): Run[] => runs.filter(keep).sort(newestFirst);
  return [
    ...group((r) => r.state === "running"),
    ...group((r) => r.state === "queued"),
    ...group((r) => ranInLastDay(r, now)),
  ];
}

/**
 * The heaviest run of the last day: the done run with the most CPU-seconds, among the runs `dayRuns` lists as
 * finished. A failed, cancelled, running or queued run is never it, nor a done run without a finite `cpu`. A tie
 * goes to the newer run. Null when there is none.
 */
export function heaviest(runs: Run[], now: Date): Run | null {
  let best: Run | null = null;
  let most = -Infinity;
  for (const r of runs) {
    const cpu = fin(r.cpu);
    if (r.state !== "done" || cpu === null || !ranInLastDay(r, now)) continue;
    if (best === null || cpu > most || (cpu === most && newestFirst(r, best) < 0)) {
      best = r;
      most = cpu;
    }
  }
  return best;
}

/** The runs running now, in the order given. */
export function runningRuns(runs: Run[]): Run[] {
  return runs.filter((r) => r.state === "running");
}

/**
 * One frame of the replay. The day is `now − 24 h … now` and `frac` is how far the hand has come, 0 to 1 (a
 * value outside that, or one that is not a number, is held inside the day). `at` is the hand's instant, to the
 * millisecond. `active` is the runs the hand is inside, newest first: those of the day with both a start and an
 * end where start ≤ at < end. A run still going has no end, so it is never among them; nor is a run that began
 * before the day, which the list does not show either.
 */
export function replayFrame(runs: Run[], now: Date, frac: number): { at: Date; active: Run[] } {
  const f = Number.isNaN(frac) ? 0 : Math.min(1, Math.max(0, frac));
  const at = new Date(Math.round(now.getTime() - DAY_MS + f * DAY_MS));
  const t = at.getTime();
  const active = runs
    .filter((r) => {
      if (!ranInLastDay(r, now)) return false;
      const start = instant(r.start), end = instant(r.end);
      return start !== null && end !== null && start <= t && t < end;
    })
    .sort(newestFirst);
  return { at, active };
}
