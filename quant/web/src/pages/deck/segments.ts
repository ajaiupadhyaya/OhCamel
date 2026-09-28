/**
 * Seven-segment readouts: which bars each character lights, and how the lamp panel's
 * counters turn numbers into characters. Pure (segments.test.ts); SevenSeg.tsx draws them.
 *
 * Segments, as on every such display:   aaa
 *                                       f   b
 *                                        ggg
 *                                       e   c
 *                                        ddd
 * ":" and " " are drawn by SevenSeg as a colon and a blank cell.
 */

export const SEGS: Record<string, string> = {
  "0": "abcdef",
  "1": "bc",
  "2": "abged",
  "3": "abgcd",
  "4": "fgbc",
  "5": "afgcd",
  "6": "afgedc",
  "7": "abc",
  "8": "abcdefg",
  "9": "abcdfg",
  "-": "g",
  " ": "",
};

export const litSegments = (ch: string) => SEGS[ch] ?? "";

const finite = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const pad2 = (n: number) => String(n).padStart(2, "0");

export interface Counter {
  digits: string; // characters for the display: 0-9, "-", ":", " "
  unit: string; // what the digits are in, said beside the display
  sign?: "gain" | "loss" | null;
  text: string; // the same reading in words, for screen readers and the title
}

const BLANK = (width: number, unit: string, text: string): Counter => ({ digits: "-".repeat(width), unit, sign: null, text });

/** Whole dollars, signed, at least `width` digits; past 9 digits it reads in $ thousands. */
export function moneyCounter(usd: number | null | undefined, width = 6): Counter {
  if (!finite(usd)) return BLANK(width, "USD", "not available");
  const neg = usd < 0;
  let n = Math.round(Math.abs(usd));
  let unit = "USD";
  if (n >= 1e9) {
    n = Math.round(n / 1000);
    unit = "USD thousands";
  }
  const body = String(n).padStart(width, "0");
  return {
    digits: (neg && n !== 0 ? "-" : "") + body,
    unit,
    sign: n === 0 ? null : neg ? "loss" : "gain",
    text: `${neg && n !== 0 ? "minus " : ""}$${Math.round(Math.abs(usd)).toLocaleString("en-US")}`,
  };
}

/** Seconds as MM:SS under an hour, HH:MM under 100 hours, dashes beyond. */
export function ageCounter(seconds: number | null | undefined): Counter {
  if (!finite(seconds) || seconds < 0) return { digits: "--:--", unit: "min:s", sign: null, text: "not available" };
  const s = Math.floor(seconds);
  if (s < 3600) return { digits: `${pad2(Math.floor(s / 60))}:${pad2(s % 60)}`, unit: "min:s", text: `${Math.floor(s / 60)} min ${s % 60} s` };
  const m = Math.floor(s / 60);
  if (m < 100 * 60) return { digits: `${pad2(Math.floor(m / 60))}:${pad2(m % 60)}`, unit: "h:min", text: `${Math.floor(m / 60)} h ${m % 60} min` };
  return { digits: "--:--", unit: "h:min", text: "over 99 hours" };
}

/**
 * Time to the close while the session is open, else to the next open, as HH:MM (minutes
 * rounded up, so "00:00" is only shown at the bell). Null clock or missing time → dashes.
 */
export function sessionCountdown(clock: { is_open: boolean; next_open: string | null; next_close: string | null } | null | undefined, nowMs: number): Counter & { label: string } {
  const label = clock ? (clock.is_open ? "to the close" : "to the open") : "session clock";
  const iso = clock ? (clock.is_open ? clock.next_close : clock.next_open) : null;
  const t = iso ? Date.parse(iso) : NaN;
  if (!Number.isFinite(t)) return { digits: "--:--", unit: "h:min", sign: null, text: clock ? "not available" : "no session clock for this source", label };
  const mins = Math.max(0, Math.ceil((t - nowMs) / 60000));
  if (mins >= 100 * 60) return { digits: "--:--", unit: "h:min", sign: null, text: `over 99 hours ${label}`, label };
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return { digits: `${pad2(h)}:${pad2(m)}`, unit: "h:min", sign: null, text: `${h} h ${m} min ${label}`, label };
}
