/**
 * The Machine's number formatters: every figure the page prints goes through one of these.
 *
 * Each returns an em dash for a missing value (null or undefined) and for a number that is not finite
 * (NaN or an infinity): a reading the page cannot show is a dash, never the word "NaN". A negative is
 * written with the typographic minus (U+2212), and a value that rounds to zero carries no sign.
 */
import { EM_DASH } from "../../lib/format";

const MINUS = "−";

const ok = (x: number | null | undefined): x is number => typeof x === "number" && Number.isFinite(x);

const formats = new Map<number, Intl.NumberFormat>();
/** en-US grouping at exactly `d` places. A formatter per `d`, made once. */
function format(d: number): Intl.NumberFormat {
  let f = formats.get(d);
  if (!f) {
    f = new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
    formats.set(d, f);
  }
  return f;
}

/** The magnitude of `x` at `d` places, and whether `x` is below zero once rounded (so -0.0004 is not "-0.0"). */
function magnitude(x: number, d: number): { body: string; negative: boolean } {
  const body = format(d).format(Math.abs(x));
  return { body, negative: x < 0 && /[1-9]/.test(body) };
}

/** 1234567 -> "1,234,567"; 1234.5 at 1 place -> "1,234.5". */
export function num(x: number | null | undefined, d = 0): string {
  if (!ok(x)) return EM_DASH;
  const { body, negative } = magnitude(x, d);
  return negative ? MINUS + body : body;
}

/**
 * A fraction as a percent: 0.034 -> "3.4%". A negative is "−3.4%". With `signed`, a gain (or zero) is
 * "+3.4%"; a level such as volatility is never signed, so it takes the default.
 */
export function pct(x: number | null | undefined, d = 1, signed = false): string {
  if (!ok(x)) return EM_DASH;
  const { body, negative } = magnitude(x * 100, d);
  return `${negative ? MINUS : signed ? "+" : ""}${body}%`;
}

/** An hour of the day on a 24-hour clock as 12-hour time: 18.5 -> "6:30 PM". It wraps round midnight, so 24 is "12:00 AM". */
export function hm(h: number | null | undefined): string {
  if (!ok(h)) return EM_DASH;
  const day = 24 * 60;
  const minutes = ((Math.round(h * 60) % day) + day) % day;
  const hour = Math.floor(minutes / 60);
  const minute = minutes % 60;
  return `${hour % 12 || 12}:${String(minute).padStart(2, "0")} ${hour < 12 ? "AM" : "PM"}`;
}

/**
 * A span in seconds: 40 -> "40 s", 3846 -> "64 min" (minutes up to two hours, so a 92-minute sweep reads as
 * 92), 7300 -> "2 h 2 min", 7200 -> "2 h". It rounds before it picks a unit, so it never says "60 s" or
 * "120 min"; a negative span (an end before its start) is no time.
 */
export function dur(s: number | null | undefined): string {
  if (!ok(s)) return EM_DASH;
  const secs = Math.max(0, Math.round(s));
  if (secs < 60) return `${secs} s`;
  const minutes = Math.round(secs / 60);
  if (minutes < 120) return `${minutes} min`;
  const rest = minutes % 60;
  return rest === 0 ? `${Math.floor(minutes / 60)} h` : `${Math.floor(minutes / 60)} h ${rest} min`;
}

/** A counted noun: (1, "job") -> "1 job", (2, "job") -> "2 jobs". `many` is for a plural that is not `word` + "s". */
export function plural(n: number | null | undefined, word: string, many = `${word}s`): string {
  if (!ok(n)) return EM_DASH;
  return `${num(n)} ${n === 1 ? word : many}`;
}
