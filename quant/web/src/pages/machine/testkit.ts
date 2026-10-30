/**
 * Test-only helpers for the Machine's copy (readings, the tour): imported only by tests.
 *
 * The honesty check: every number a reading prints must be one the response carries, as a reading prints it.
 * `allowedNumbers` builds that set from a payload and `numbersIn` finds the numbers in a text, both through
 * the same pattern, so "3.4%" in a reading is matched against "3.4%" from `pct`. The set holds only what the
 * readings can print: the fields they read, through the formatters they use, and the counts they can make,
 * so a figure the payload does not carry ("23 strategies" on a desk of 2, "99%") fails.
 */
import { hm, num, nyDay, nyTime, pct } from "./fmt";
import { parseDailyNote, parseOverlayNote } from "./notes";
import { cpuTotal, jobRuns, reason, share } from "./prose";
import type { Reading } from "./readings";
import type { MachineOut } from "./types";
import { countKinds, directReaders, indexGraph, webOf } from "./web";

/**
 * A number as text: an optional sign, digits with thousands grouped, decimals, a time's minutes (and AM or
 * PM), a percent, and a day's month ("28 Oct" is one token, so a date never vouches for a bare "28"). It finds
 * a number wherever it stands, inside a word too ("a01" gives "01").
 */
const NUMBER = /[−+]?\d+(?:,\d{3})*(?:\.\d+)?(?::\d{2}(?: [AP]M)?)?%?(?: (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b)?/g;

/** Every number in `text`, in order: "+41.3% at 6:30 PM on 28 Oct" gives ["+41.3%", "6:30 PM", "28 Oct"]. */
export function numbersIn(text: string): string[] {
  return text.match(NUMBER) ?? [];
}

type Fmt = (x: number) => string;
const num0: Fmt = (x) => num(x, 0), num2: Fmt = (x) => num(x, 2);
const pct1: Fmt = (x) => pct(x, 1), pct2: Fmt = (x) => pct(x, 2), signed: Fmt = (x) => pct(x, 1, true);

/** Each numeric field a reading prints, wherever it sits in a node's `m`, and how. */
const PRINTED: Record<string, Fmt[]> = {
  ret1y: [signed], vol: [pct1], n: [num0], keys: [num0], behind: [num0], fires: [hm],
  cells: [num0], trials: [num0], p_high: [num2], weeks: [num0], atm30: [pct1], slices: [num0], names: [num0], strategies: [num0],
  combos: [num0], n_combos: [num0], oos_sharpe_ann: [num2], psr: [num2], dsr_farm: [num2], pbo: [num2],
  mcs_rate: [pct1], mean_rank: [num2], var: [pct2], es: [pct2], paths: [num0], var_usd: [num0], es_usd: [num0], rank: [num0],
  capital: [share], incubation_cap: [share], vol_target: [share], positions: [num0], limits: [num0],
};
/** The lens tables a reading prints from, column by column: how each figure is printed, "q" for a name it quotes. */
const ROWS: Record<string, (Fmt[] | "q" | null)[]> = {
  peers: ["q", [num2]],
  board: ["q", "q", [num2], [num2], null, null, null],
  models: ["q", [pct1], null, [num0]],
  estimators: ["q", "q", [pct1], null],
  measures: ["q", [num0], [pct2], [pct2], [num0]],
  deciles: [null, [pct2]],
};
/** The timestamps and dates a reading prints, as `nyDay` and `nyTime` write them. */
const TIMES = new Set(["asof", "ok", "finished", "summary_asof", "date"]);
/** The strings a reading quotes whole: a reason, a verdict's detail, a scope, a name; never a note (those are caveats). */
const QUOTED = new Set(["why", "detail", "reason", "scope", "summary_why", "lens_why", "verdict", "symbols", "trips_on", "kind", "underlying", "when", "producer", "state", "kill"]);
const FINAL = ["done", "failed", "cancelled"];

/**
 * The numbers the readings of `out` may print, read at `now` (the payload's own time by default):
 * - each field a reading prints (`PRINTED`, `ROWS`), through the formatter it is printed with;
 * - the figures parsed from notes (`parseDailyNote`, `parseOverlayNote`);
 * - each timestamp a reading prints, through `nyDay` and `nyTime`;
 * - the numbers inside the strings a reading quotes whole (a node's label, name and `why`, `QUOTED`), with any
 *   date in them written as `reason` writes it;
 * - the counts a reading can make: nodes by kind and track, each node's neighbours and web by kind, its direct
 *   readers, a job's fires, the farm's board rows and passes and its strategies, and each job kind's runs by
 *   state with their CPU-seconds, over the same last day the readings use (`jobRuns`, `cpuTotal`).
 */
export function allowedNumbers(out: MachineOut, now = new Date(out.asof)): Set<string> {
  const allowed = new Set<string>();
  const add = (s: string): void => numbersIn(s).forEach((n) => allowed.add(n));
  const quote = (s: string): void => add(reason(s));
  const count = (k: number): void => add(num0(k));

  const walk = (v: unknown, key: string): void => {
    const cols = ROWS[key];
    if (cols && Array.isArray(v)) {
      for (const row of v) {
        if (!Array.isArray(row)) continue;
        row.forEach((x, p) => {
          const how = cols[p];
          if (how === "q" && typeof x === "string") quote(x);
          else if (Array.isArray(how) && typeof x === "number" && Number.isFinite(x)) how.forEach((f) => add(f(x)));
        });
      }
    } else if (typeof v === "number" && Number.isFinite(v)) (PRINTED[key] ?? []).forEach((f) => add(f(v)));
    else if (typeof v === "string") {
      if (TIMES.has(key)) [nyDay(v), nyTime(v)].forEach(add);
      if (QUOTED.has(key)) quote(v);
    } else if (Array.isArray(v)) v.forEach((x) => walk(x, key));
    else if (v && typeof v === "object") Object.entries(v).forEach(([k, x]) => walk(x, k));
  };

  const gi = indexGraph(out);
  const kinds = (ids: Iterable<number>): void => Object.values(countKinds(gi, ids)).forEach(count);
  kinds(out.nodes.keys());
  const tracks = new Map<unknown, number>();
  out.nodes.forEach((n) => n.k === "series" && tracks.set(n.m.track, (tracks.get(n.m.track) ?? 0) + 1));
  tracks.forEach(count);
  count(out.nodes.filter((n, i) => n.k === "strategy" && gi.up[i].some((j) => out.nodes[j].k === "result")).length);

  out.nodes.forEach((n, i) => {
    [n.l, n.n, n.why ?? ""].forEach(quote);
    walk(n.m, "");
    if (n.id.startsWith("ce:")) Object.keys(n.m).forEach(quote);   // a covariance estimator's universes, named in its reading
    const notes = Array.isArray(n.m.notes) ? n.m.notes.filter((x): x is string => typeof x === "string") : [];
    Object.values(parseDailyNote(notes) ?? {}).forEach(count);
    const o = parseOverlayNote(notes);
    if (o) {
      add(pct1(o.forecast));
      add(share(o.target));
      add(num2(o.scale));
      if (o.sessions !== null) count(o.sessions);
    }
    kinds(gi.up[i]);
    kinds(gi.down[i]);
    const web = webOf(gi, i, "web");
    kinds(web.up.keys());
    kinds(web.down.keys());
    count(directReaders(gi, i).length);
    if (Array.isArray(n.m.fires)) count(n.m.fires.length);
    const lens = n.m.lens as { board?: unknown } | undefined;
    if (lens && Array.isArray(lens.board)) {
      count(lens.board.length);
      count(lens.board.filter((r) => Array.isArray(r) && r[1] === "PASS").length);
    }
  });

  const jobKinds = new Set([...out.runs.map((r) => r.kind), ...out.nodes.filter((n) => n.k === "job").map((n) => String(n.m.kind))]);
  for (const kind of jobKinds) {
    const { ran, running, queued } = jobRuns(out.runs, kind, now);
    [ran.length, running.length, queued.length, ...FINAL.map((s) => ran.filter((r) => r.state === s).length)].forEach(count);
    const cpu = cpuTotal(ran);
    if (cpu !== null) count(cpu);
    running.forEach((r) => typeof r.progress === "number" && add(pct(r.progress, 0)));
  }
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
