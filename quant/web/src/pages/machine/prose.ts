/**
 * The kit the Machine's readings are written with (readings.ts, products.ts): text segments, defensive reads
 * of the payload, and the few phrases about the graph that every kind shares. Pure, no DOM.
 */
import { instant, num, nyDay, nyTime, nyYear, pct, plural } from "./fmt";
import type { MNode, Run } from "./types";
import { directReaders, webOf } from "./web";
import type { GraphIndex } from "./web";

/** Plain text, a bold figure, or a link to the node `ref` (by id) that reads `t`. */
export type Seg = { t: string } | { b: string } | { ref: string; t: string };
export interface Reading {
  title: string;
  paras: Seg[][];
  /** The system's own notes on this thing, quoted whole. */
  caveats: string[];
}
export interface ReadingCtx {
  runs: Run[];
  now: Date;
}

// ------------------------------------------------------------------ segments
export type Bit = Seg | string | false | null | undefined | readonly Bit[];

const isText = (s: Seg): s is { t: string } => "t" in s && !("ref" in s);
/** Segments from bits: a string is plain text, false/null/undefined/"" drop out, arrays flatten, adjacent text joins. */
export function segs(...bits: Bit[]): Seg[] {
  const out: Seg[] = [];
  const add = (x: Bit): void => {
    if (x === false || x === null || x === undefined || x === "") return;
    if (Array.isArray(x)) return (x as readonly Bit[]).forEach(add);
    const s: Seg = typeof x === "string" ? { t: x } : (x as Seg);
    const last = out[out.length - 1];
    if (isText(s) && last && isText(last)) out[out.length - 1] = { t: last.t + s.t };
    else out.push(s);
  };
  bits.forEach(add);
  return out;
}
/** A sentence or paragraph written as a template: `S\`It ran ${B(num(n))} times.\``. */
export function S(strings: TemplateStringsArray, ...vals: Bit[]): Seg[] {
  return segs(...strings.flatMap((s, k): Bit[] => (k < vals.length ? [s, vals[k]] : [s])));
}
export const B = (b: string): Seg => ({ b });
/** "a", "a and b", "a, b and c"; a dropped bit is no item. */
export function list(items: Bit[]): Seg[] {
  const kept = items.filter((x) => x !== false && x !== null && x !== undefined && x !== "");
  return segs(...kept.map((x, k) => [k === 0 ? "" : k === kept.length - 1 ? " and " : ", ", x]));
}
/** A paragraph without the trailing space its last sentence left. */
export function trim(p: Seg[]): Seg[] {
  const last = p[p.length - 1];
  return last && isText(last) ? [...p.slice(0, -1), ...segs(last.t.trimEnd())] : p;
}
/** The paragraph with its first letter capitalised. */
export function capital(p: Seg[]): Seg[] {
  const [f, ...rest] = p;
  return f && isText(f) ? [{ t: f.t.charAt(0).toUpperCase() + f.t.slice(1) }, ...rest] : p;
}
/** The paragraphs that say something, each without a trailing space. */
export const paras = (...ps: (Seg[] | false | null | undefined)[]): Seg[][] =>
  ps.filter((p): p is Seg[] => Array.isArray(p) && p.length > 0).map(trim);

// ------------------------------------------------------------------ reading the payload defensively
export const fin = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
export const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
export const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
export const rows = (v: unknown): unknown[][] => (Array.isArray(v) ? v.filter((x): x is unknown[] => Array.isArray(x)) : []);
export const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
export const known = (s: string): boolean => s !== "—";

/** A share of capital or a cap at the fewest places that write it exactly (0.6 -> "60%", 0.025 -> "2.5%"). */
export function share(x: number): string {
  for (const d of [0, 1]) if (Math.abs(Math.round(x * 10 ** (d + 2)) - x * 10 ** (d + 2)) < 1e-6) return pct(x, d);
  return pct(x, 2);
}
/** One share, or the range when they differ: "2.5%", "1% to 2.5%". */
export function shares(xs: number[]): Seg[] {
  const lo = Math.min(...xs), hi = Math.max(...xs);
  return lo === hi ? [B(share(lo))] : S`${B(share(lo))} to ${B(share(hi))}`;
}
/** "4:30 PM on 28 Oct", or nothing when the timestamp does not read. */
export function at(iso: unknown): Seg[] {
  const t = nyTime(str(iso)), d = nyDay(str(iso));
  return known(t) && known(d) ? S`${B(t)} on ${B(d)}` : [];
}

const STAMP = /\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?/g;
/**
 * A reason the server wrote for the page, to quote: any date or instant in it written as the page writes
 * times ("the desk.report artifact of 4:30 PM on 28 Oct is stale"), never as raw ISO text. A day outside the
 * payload's own year keeps its year ("starts 2 Jan 2019"); one that does not parse is left as written.
 */
export function reason(gi: GraphIndex, s: string): string {
  const year = nyYear(gi.out.asof);
  return s.replace(STAMP, (m) => {
    const t = nyTime(m), d = nyDay(m), y = nyYear(m);
    if (!known(d)) return m;
    const day = y !== null && y === year ? d : `${d} ${y}`;
    return known(t) ? `${t} on ${day}` : day;
  });
}

// ------------------------------------------------------------------ the graph
export const node = (gi: GraphIndex, i: number): MNode => gi.out.nodes[i];
/** How a node is named in running text: its plain name where its label is a code or a slug. */
function refText(n: MNode): string {
  if (n.k === "product" || n.k === "result") return n.n || n.l;
  if (n.k === "book") return "the book";
  if (n.k === "kill") return "the kill switch";
  return n.l || n.id;
}
/** A link to node `id`, or plain text when the graph has no such node. */
export function ref(gi: GraphIndex, id: string, text?: string): Seg {
  const i = gi.byId.get(id);
  return i === undefined ? { t: text ?? id } : { ref: id, t: text ?? refText(node(gi, i)) };
}
export const refAt = (gi: GraphIndex, i: number, text?: string): Seg => ref(gi, node(gi, i).id, text);
export const ups = (gi: GraphIndex, i: number, k: MNode["k"]): number[] => gi.up[i].filter((j) => node(gi, j).k === k);
export const downs = (gi: GraphIndex, i: number, k: MNode["k"]): number[] => gi.down[i].filter((j) => node(gi, j).k === k);
export const strategyCount = (gi: GraphIndex): number => gi.out.nodes.filter((n) => n.k === "strategy").length;

/** "1 of the desk's 2 strategies" */
export function ofDesk(gi: GraphIndex, k: number): Seg[] {
  const all = strategyCount(gi);
  return S`${B(num(k))} of the desk's ${num(all)} ${all === 1 ? "strategy" : "strategies"}`;
}
/** What node `i` reaches downstream, within the web the page lights. */
export function reach(gi: GraphIndex, i: number): { down: Map<number, number>; st: number; book: boolean } {
  const down = webOf(gi, i, "feeds").down;
  const kinds = [...down.keys()].map((j) => node(gi, j).k);
  return { down, st: kinds.filter((k) => k === "strategy").length, book: kinds.includes("book") };
}
/** "Downstream it reaches 1 of the desk's 2 strategies and the book." */
export function reaches(gi: GraphIndex, i: number, lead: string): Seg[] {
  const { st, book } = reach(gi, i);
  if (!st && !book) return [];
  return S`${lead} reaches ${list([st > 0 && ofDesk(gi, st), book && "the book"])}.`;
}
/** "1 job reads it directly: Vol forecast league." */
export function readers(gi: GraphIndex, i: number): Seg[] {
  const r = directReaders(gi, i);
  if (!r.length) return segs("No job reads it directly.");
  return S`${B(plural(r.length, "job"))} ${r.length === 1 ? "reads" : "read"} it directly: ${list(r.map((j) => refAt(gi, j)))}.`;
}
/** A covariance estimator by its node's label ("lw constant corr"), or its id in words when there is no node. */
export function estimator(gi: GraphIndex, e: string): Seg {
  const i = gi.byId.get(`ce:${e}`);
  return ref(gi, `ce:${e}`, (i !== undefined && node(gi, i).l) || e.replace(/_/g, " "));
}

// ------------------------------------------------------------------ runs
const FINAL = new Set<Run["state"]>(["done", "failed", "cancelled"]);
/** The last day, in milliseconds. */
export const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Whether `r` is among the runs of the day before `now`: the one rule for "the last day", which the readings
 * (`jobRuns`) and the day tab (day.ts) both use, so they cannot disagree on which runs fall in it (ruling 17).
 * A finished run is dated by start, else by end, and counted when neither is known yet (a run seen only
 * through the job stream) or one does not read. A run cancelled while still queued (no start) never ran, so it
 * is not among them. The window is open at the later end: a run dated after `now` (the server's clock may lead
 * the page's) is in the day. A queued or running run is not "ran" here; those are listed by state.
 */
export function ranInLastDay(r: Run, now: Date): boolean {
  if (!FINAL.has(r.state) || (r.state === "cancelled" && r.start === null)) return false;
  return !((instant(r.start ?? r.end) ?? Infinity) < now.getTime() - DAY_MS);
}

/**
 * A job kind's runs in `runs`, by state (ruling 17). `ran` is the finished runs of the day before `now`
 * (`ranInLastDay`); `running` and `queued` are listed whenever they are, whatever their start.
 */
export function jobRuns(runs: Run[], kind: string, now: Date): { ran: Run[]; running: Run[]; queued: Run[] } {
  const mine = runs.filter((r) => r.kind === kind);
  return {
    ran: mine.filter((r) => ranInLastDay(r, now)),
    running: mine.filter((r) => r.state === "running"),
    queued: mine.filter((r) => r.state === "queued"),
  };
}
/** The CPU-seconds of `runs` added up, or null when none of them has a figure. */
export function cpuTotal(runs: Run[]): number | null {
  const cpus = runs.map((r) => fin(r.cpu)).filter((c): c is number => c !== null);
  return cpus.length ? cpus.reduce((a, c) => a + c, 0) : null;
}
