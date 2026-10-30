/**
 * Test-only helpers for the Machine's copy (readings, the tour): imported only by tests.
 *
 * The honesty check: every number a reading prints must be one the response carries, as a reading prints
 * it. `allowedNumbers` builds that set from a payload, and `numbersIn` finds the numbers in a text, both
 * through the same pattern, so "3.4%" in a reading is matched against "3.4%" from `pct`.
 */
import { hm, num, nyDay, nyTime, pct } from "./fmt";
import { parseDailyNote, parseOverlayNote } from "./notes";
import type { Reading } from "./readings";
import type { MachineOut } from "./types";

/**
 * A number as text: an optional sign, digits with thousands grouped, decimals, a time's minutes and a percent.
 * It finds a number wherever it stands, inside a word too ("a01" gives "01").
 */
const NUMBER = /[−+]?\d+(?:,\d{3})*(?:\.\d+)?(?::\d{2})?%?/g;

/** Every number in `text`, in order: "+41.3% at 6:30 PM" gives ["+41.3%", "6:30"]. */
export function numbersIn(text: string): string[] {
  return text.match(NUMBER) ?? [];
}

/** Each formatter and precision the readings print a figure with. */
const FORMATS: ((x: number) => string)[] = [
  (x) => num(x, 0), (x) => num(x, 2),
  (x) => pct(x, 0), (x) => pct(x, 1), (x) => pct(x, 2), (x) => pct(x, 1, true),
  hm,
];
const ISO = /^\d{4}-\d{2}-\d{2}(?:T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$/;
/** Strings the readings never print: notes are quoted only as caveats (checked whole); ids and cron are not shown. */
const UNPRINTED = new Set(["notes", "art", "code", "cron"]);
const FINAL = new Set(["done", "failed", "cancelled"]);

/** Every leaf of `v` that is a finite number or a string, with the key it sits under. */
function* leaves(v: unknown, key = ""): Generator<[string, number | string]> {
  if (typeof v === "number" && Number.isFinite(v)) yield [key, v];
  else if (typeof v === "string") yield [key, v];
  else if (Array.isArray(v)) for (const x of v) yield* leaves(x, key);
  else if (v && typeof v === "object") for (const [k, x] of Object.entries(v)) yield* leaves(x, k);
}

/**
 * The numbers a reading of `out` may print. The brief's set, every numeric value in the payload through each
 * formatter and precision the readings use plus the counts 0 … nodes + edges, and what the readings also read
 * from the payload:
 * - the figures parsed from notes (`parseDailyNote`, `parseOverlayNote`), through each formatter;
 * - every date and timestamp, as `nyDay` and `nyTime` print it;
 * - each job kind's CPU-seconds summed over its finished runs (the job reading's one total);
 * - the numbers inside a node's own words (its label, name, `why`, and the strings in its `m` the readings
 *   quote whole: a verdict's detail, a skip reason, a scope), never its notes, which are only ever quoted
 *   whole as caveats and checked as such.
 */
export function allowedNumbers(out: MachineOut): Set<string> {
  const allowed = new Set<string>();
  const add = (s: string): void => numbersIn(s).forEach((n) => allowed.add(n));
  const figure = (x: number): void => FORMATS.forEach((f) => add(f(x)));

  for (const [, v] of leaves(out)) if (typeof v === "number") figure(v);
  for (const node of out.nodes) {
    const notes = Array.isArray(node.m.notes) ? node.m.notes.filter((x): x is string => typeof x === "string") : [];
    Object.values(parseDailyNote(notes) ?? {}).forEach(figure);
    const o = parseOverlayNote(notes);
    if (o) [o.scale, o.target, o.forecast, o.sessions].forEach((x) => typeof x === "number" && figure(x));
    [node.l, node.n, node.why ?? ""].forEach(add);
    for (const [k, v] of leaves(node.m)) if (typeof v === "string" && !UNPRINTED.has(k) && !ISO.test(v)) add(v);
  }
  for (const [, v] of leaves(out)) {
    if (typeof v === "string" && ISO.test(v)) {
      add(nyDay(v));
      add(nyTime(v));
    }
  }
  const cpu = new Map<string, number>();
  for (const r of out.runs) {
    if (FINAL.has(r.state) && typeof r.cpu === "number" && Number.isFinite(r.cpu)) cpu.set(r.kind, (cpu.get(r.kind) ?? 0) + r.cpu);
  }
  cpu.forEach(figure);
  for (let k = 0; k <= out.nodes.length + out.edges.length; k++) allowed.add(String(k));
  return allowed;
}

/** A reading's title and paragraphs as plain text, one line each. Caveats are left out: they are quotes, checked whole. */
export function flatten(r: Reading): string {
  return [r.title, ...r.paras.map((p) => p.map((s) => ("b" in s ? s.b : s.t)).join(""))].join("\n");
}

/** The node ids a reading links to, in order. */
export function refsOf(r: Reading): string[] {
  return r.paras.flat().flatMap((s) => ("ref" in s ? [s.ref] : []));
}
