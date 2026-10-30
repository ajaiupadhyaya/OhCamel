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

// ---------------------------------------------------------------- New York days and times
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;
/** A wire timestamp with no offset: the warehouse writes `last_ok_at` naive, in UTC (warehouse/freshness.py). */
const NAIVE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/;
const NY = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric", hourCycle: "h23",
});

type Day = { year: number; month: number; day: number };

/** A date ("2026-10-27") as its calendar day, checked to be a real one; null otherwise. */
function calendarDay(v: string): Day | null {
  const m = DATE_ONLY.exec(v);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const t = new Date(Date.UTC(y, mo - 1, d));
  return t.getUTCFullYear() === y && t.getUTCMonth() === mo - 1 && t.getUTCDate() === d ? { year: y, month: mo, day: d } : null;
}

/**
 * A timestamp from the wire as epoch milliseconds, one with no offset read as UTC; null for a date (it names
 * a day, not an instant) and for anything that does not parse. The one rule for every instant the page reads.
 */
export function instant(v: string | null | undefined): number | null {
  if (typeof v !== "string" || DATE_ONLY.test(v)) return null;
  const t = Date.parse(NAIVE.test(v) ? `${v}Z` : v);
  return Number.isNaN(t) ? null : t;
}

/** A timestamp as its New York date, hour and minute; null when it is not one. */
function nyParts(v: string): (Day & { hour: number; minute: number }) | null {
  const t = instant(v);
  if (t === null) return null;
  const p = Object.fromEntries(NY.formatToParts(new Date(t)).map((x) => [x.type, x.value]));
  return { year: Number(p.year), month: Number(p.month), day: Number(p.day), hour: Number(p.hour) % 24, minute: Number(p.minute) };
}

/** The year of a date, or of a timestamp in New York; null for anything else. */
export function nyYear(v: string | null | undefined): number | null {
  if (typeof v !== "string") return null;
  return (calendarDay(v) ?? nyParts(v))?.year ?? null;
}

/**
 * The New York day of a date or timestamp from the wire: "2026-10-28T01:48:11Z" -> "27 Oct" (evening in New
 * York). A date ("2026-10-27") is the calendar day it names, in no time zone. Anything else is an em dash.
 */
export function nyDay(v: string | null | undefined): string {
  if (typeof v !== "string") return EM_DASH;
  const d = calendarDay(v) ?? nyParts(v);
  return d ? `${d.day} ${MONTHS[d.month - 1]}` : EM_DASH;
}

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
/**
 * The New York weekday of a date or timestamp from the wire, as a word: "2026-10-29T02:05:00Z" -> "Wednesday"
 * (evening in New York). A date is the weekday of the calendar day it names. Anything else is an em dash.
 */
export function nyWeekday(v: string | null | undefined): string {
  if (typeof v !== "string") return EM_DASH;
  const d = calendarDay(v) ?? nyParts(v);
  return d ? WEEKDAYS[new Date(Date.UTC(d.year, d.month - 1, d.day)).getUTCDay()] : EM_DASH;
}

/** The New York time of day of a timestamp from the wire, as `hm` writes it: "2026-10-28T20:30:12Z" -> "4:30 PM". A date has none. */
export function nyTime(v: string | null | undefined): string {
  if (typeof v !== "string") return EM_DASH;
  const p = nyParts(v);
  return p ? hm(p.hour + p.minute / 60) : EM_DASH;
}
