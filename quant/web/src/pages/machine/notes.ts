/**
 * The few notes a reading parses: the figures two jobs write into their artifact's own notes, read back
 * exactly as the jobs write them. A note of another shape parses to null, and the reading then says less;
 * it never guesses a figure.
 */

/** What one daily signals run did, from the first note `signals/daily.py` writes (daily.py:482). */
export interface DailyCounts {
  written: number;
  late: number;
  again: number;
  held: number;
  present: number;
  refused: number;
}

const DAILY = /^(\d+) written, (\d+) written late, (\d+) written again .*?, (\d+) held, (\d+) already present, (\d+) refused/;

/** The six counts of the first note, or null when the first note is not that note. */
export function parseDailyNote(notes: string[]): DailyCounts | null {
  const m = typeof notes[0] === "string" ? DAILY.exec(notes[0]) : null;
  if (!m) return null;
  const [written, late, again, held, present, refused] = m.slice(1, 7).map(Number);
  return { written, late, again, held, present, refused };
}

/** The exposure overlay's scale and what it came from, from `signals/overlay.py`'s note (overlay.py:430). */
export interface OverlayScale {
  scale: number;
  target: number;
  forecast: number;
  estimator: string | null;
  sessions: number | null;
}

const OVERLAY = /exposure_scale ([\d.]+) = min\(1, ([\d.]+) \/ ([\d.]+)\)(?:; covariance (\S+) on (\d+) sessions)?/;

/** The first note that states the exposure scale, read; null when none does or a figure is not a number. */
export function parseOverlayNote(notes: string[]): OverlayScale | null {
  for (const note of notes) {
    const m = typeof note === "string" ? OVERLAY.exec(note) : null;
    if (!m) continue;
    const [scale, target, forecast] = [m[1], m[2], m[3]].map(Number);
    if (![scale, target, forecast].every(Number.isFinite)) return null;
    return { scale, target, forecast, estimator: m[4] ?? null, sessions: m[5] === undefined ? null : Number(m[5]) };
  }
  return null;
}

// overlay.py:240 and :246: the covariance league had no artifact, or no winner for the desk's universe
const FALLBACK = /^(?:no cov\.league artifact|cov\.league \S+ has no rank-1 row for \S+): (\S+)/;

/** The estimator the overlay fell back to, when one of its notes says it did; otherwise null. */
export function parseOverlayFallback(notes: string[]): string | null {
  for (const note of notes) {
    const m = typeof note === "string" ? FALLBACK.exec(note) : null;
    if (m) return m[1];
  }
  return null;
}
