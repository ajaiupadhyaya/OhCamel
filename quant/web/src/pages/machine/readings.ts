/**
 * Readings: what a selected thing is and what it did, in plain words (spec §6.5). Pure: a node and the graph
 * in, text segments out, for React to render as text. Every figure is read from the response through `fmt`;
 * a figure the response lacks is left unsaid, never shown as a dash; the notes a job writes about itself are
 * quoted whole as `caveats`. The approved prototype's copy is the wording reference, not its numbers.
 */
import { hm, num, nyDay, nyTime, pct, plural } from "./fmt";
import { parseDailyNote, parseOverlayFallback, parseOverlayNote } from "./notes";
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
type Bit = Seg | string | false | null | undefined | readonly Bit[];

/** Segments from bits: a string is plain text, false/null/undefined/"" drop out, arrays flatten, adjacent text joins. */
function segs(...bits: Bit[]): Seg[] {
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
const isText = (s: Seg): s is { t: string } => "t" in s && !("ref" in s);
/** A sentence or paragraph written as a template: `S\`It ran ${B(num(n))} times.\``. */
function S(strings: TemplateStringsArray, ...vals: Bit[]): Seg[] {
  return segs(...strings.flatMap((s, k): Bit[] => (k < vals.length ? [s, vals[k]] : [s])));
}
const B = (b: string): Seg => ({ b });
/** "a", "a and b", "a, b and c". */
function list(items: Bit[]): Seg[] {
  const kept = items.filter((x) => x !== false && x !== null && x !== undefined && x !== "");
  return segs(...kept.map((x, k) => [k === 0 ? "" : k === kept.length - 1 ? " and " : ", ", x]));
}

// ------------------------------------------------------------------ reading the payload defensively
const fin = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
const rows = (v: unknown): unknown[][] => (Array.isArray(v) ? v.filter((x): x is unknown[] => Array.isArray(x)) : []);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const known = (s: string): boolean => s !== "—";

/** A share of capital or a cap at the fewest places that write it exactly (0.6 -> "60%", 0.025 -> "2.5%"). */
function share(x: number): string {
  for (const d of [0, 1]) if (Math.abs(Math.round(x * 10 ** (d + 2)) - x * 10 ** (d + 2)) < 1e-6) return pct(x, d);
  return pct(x, 2);
}
/** One share, or the range when they differ: "2.5%", "1% to 2.5%". */
function shares(xs: number[]): Seg[] {
  const lo = Math.min(...xs), hi = Math.max(...xs);
  return lo === hi ? [B(share(lo))] : S`${B(share(lo))} to ${B(share(hi))}`;
}
/** "4:30 PM on 28 Oct", or nothing when the timestamp does not read. */
function at(iso: unknown): Seg[] {
  const t = nyTime(str(iso)), d = nyDay(str(iso));
  return known(t) && known(d) ? S`${B(t)} on ${B(d)}` : [];
}

// ------------------------------------------------------------------ the graph
const node = (gi: GraphIndex, i: number): MNode => gi.out.nodes[i];
/** How a node is named in running text. */
function refText(n: MNode): string {
  if (n.k === "product") return n.n || n.l;
  if (n.k === "book") return "the book";
  if (n.k === "kill") return "the kill switch";
  return n.l || n.id;
}
/** A link to node `id`, or plain text when the graph has no such node. */
function ref(gi: GraphIndex, id: string, text?: string): Seg {
  const i = gi.byId.get(id);
  return i === undefined ? { t: text ?? id } : { ref: id, t: text ?? refText(node(gi, i)) };
}
const refAt = (gi: GraphIndex, i: number, text?: string): Seg => ref(gi, node(gi, i).id, text);
const ups = (gi: GraphIndex, i: number, k: MNode["k"]): number[] => gi.up[i].filter((j) => node(gi, j).k === k);
const downs = (gi: GraphIndex, i: number, k: MNode["k"]): number[] => gi.down[i].filter((j) => node(gi, j).k === k);
const strategyCount = (gi: GraphIndex): number => gi.out.nodes.filter((n) => n.k === "strategy").length;

/** "1 of the desk's 2 strategies" */
function ofDesk(gi: GraphIndex, k: number): Seg[] {
  const all = strategyCount(gi);
  return S`${B(num(k))} of the desk's ${num(all)} ${all === 1 ? "strategy" : "strategies"}`;
}
/** What node `i` reaches downstream, within the web the page lights. */
function reach(gi: GraphIndex, i: number): { down: Map<number, number>; st: number; book: boolean } {
  const down = webOf(gi, i, "feeds").down;
  const kinds = [...down.keys()].map((j) => node(gi, j).k);
  return { down, st: kinds.filter((k) => k === "strategy").length, book: kinds.includes("book") };
}
function reaches(gi: GraphIndex, i: number, lead: string): Seg[] {
  const { st, book } = reach(gi, i);
  if (!st && !book) return [];
  return S`${lead} reaches ${list([st > 0 && ofDesk(gi, st), book && "the book"])}.`;
}
/** "1 job reads it directly: Vol forecast league." */
function readers(gi: GraphIndex, i: number, none = "No job reads it directly."): Seg[] {
  const r = directReaders(gi, i);
  if (!r.length) return segs(none);
  return S`${B(plural(r.length, "job"))} ${r.length === 1 ? "reads" : "read"} it directly: ${list(r.map((j) => refAt(gi, j)))}.`;
}

const WHEN: Record<string, string> = { daily: "every day", weekdays: "on weekdays", weekly: "once a week", monthly: "once a month" };
/** A job's schedule: "on weekdays at 6:30 PM", or "on weekdays, 29 times a day, from 9:20 AM to 8:10 PM". */
function schedule(m: Record<string, unknown>): Seg[] {
  const when = strs(m.when).map((w) => WHEN[w] ?? w);
  const fires = (Array.isArray(m.fires) ? m.fires : []).map(fin).filter((h): h is number => h !== null).sort((a, b) => a - b);
  const w = when.length ? list(when) : null;
  if (fires.length === 0) return w ?? [];
  if (fires.length <= 3) return S`${w && [w, " "]}at ${list(fires.map((h) => B(hm(h))))}`;
  return S`${w && [w, ", "]}${B(num(fires.length))} times a day, from ${B(hm(fires[0]))} to ${B(hm(fires[fires.length - 1]))}`;
}

// ------------------------------------------------------------------ the world
const TRACKS: [string, string][] = [["s:", "SEC filings"], ["m:", "one-minute bars"], ["o:", "an option chain"]];

function ticker(gi: GraphIndex, i: number, n: MNode): Reading {
  const m = n.m;
  const count = gi.out.nodes.filter((x) => x.k === "series" && x.m.track === "daily").length;
  const writer = ups(gi, i, "job")[0];
  const when = writer === undefined ? [] : schedule(node(gi, writer).m);
  const tracks = TRACKS.filter(([p]) => gi.byId.has(p + n.l)).map(([p, word]) => ref(gi, p + n.l, word));
  const first = S`One of ${B(num(count))} tickers ${when.length ? S`whose daily closes the warehouse refreshes ${when}` : "on the warehouse's daily track"}.${
    tracks.length > 0 && S` It also has ${list(tracks)}.`}`;

  // ruling 9: a year's return only on a year of closes; ruling 10: any figure may be absent
  const closes = fin(m.n), ret = fin(m.ret1y), vol = fin(m.vol);
  const span = closes === 253 ? "the last year" : closes !== null ? `the last ${num(closes)} sessions` : null;
  const moves = span && (ret !== null
    ? S` Over ${span} it returned ${B(pct(ret, 1, true))}${vol !== null && S`, with ${B(pct(vol, 1))} annualized volatility`}.`
    : vol !== null && S` Its annualized volatility over ${span} is ${B(pct(vol, 1))}.`);

  const r = directReaders(gi, i);
  const { down, st, book } = reach(gi, i);
  let route: Seg[] = [];
  if (st > 0) route = S` ${r.length ? "Through them it" : "It"} reaches ${list([ofDesk(gi, st), book && "the book"])}.`;
  else if (book) {
    const overlay = gi.byId.get("p:signals.overlay");
    const limits = downs(gi, i, "limit");
    const ways = [
      overlay !== undefined && down.has(overlay) && S`through the ${ref(gi, "p:signals.overlay", "exposure overlay")}, which scales the whole book`,
      limits.length > 0 && S`through its own ${limits.length === 1 ? "limit" : "limits"}, ${list(limits.map((j) => refAt(gi, j)))}`,
    ].filter((x): x is Seg[] => Array.isArray(x));
    route = S` No strategy on the desk depends on it, but it still reaches the book${ways.length > 0 && [": ", segs(...ways.map((w, k) => [k ? ", and " : "", w]))]}.`;
  }

  const peers = rows(m.peers)
    .map(([t, c]) => [str(t), fin(c)] as const)
    .filter((p): p is readonly [string, number] => p[0] !== null && p[1] !== null)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3);
  return {
    title: n.n && n.n !== n.l ? `${n.l}, ${n.n}` : n.l,
    paras: [
      segs(first, moves),
      segs(readers(gi, i), route),
      peers.length > 0 && S`It moved most with ${list(peers.map(([t, c]) => [ref(gi, `d:${t}`, t), ` (${num(c, 2)})`]))}${span && ` over ${span}`}.`,
    ].filter((p): p is Seg[] => Array.isArray(p) && p.length > 0),
    caveats: [],
  };
}

const TRACK_WORD: Record<string, string> = {
  filings: "SEC filings", minute: "one-minute bars", options: "option chain", macro: "a macro series", factors: "a factor series", "13f": "a fund manager's holdings",
};
function series(gi: GraphIndex, i: number, n: MNode): Reading {
  const track = str(n.m.track) ?? "";
  const word = TRACK_WORD[track] ?? `a series on the ${track || "warehouse's"} track`;
  const of = str(n.at.of);
  const first = of ? S`This is the warehouse's record of ${ref(gi, of)}'s ${word}.` : S`This is ${word} in the warehouse.`;
  return { title: n.n || n.l, paras: [first, segs(readers(gi, i), reaches(gi, i, " Downstream it"))], caveats: [] };
}

function feed(gi: GraphIndex, i: number, n: MNode): Reading {
  const m = n.m;
  const keys = fin(m.keys), behind = fin(m.behind), asof = nyDay(str(m.asof)), pulled = at(m.ok);
  const first = segs(
    keys !== null && S`The warehouse keeps ${B(num(keys))} series from this feed. `,
    known(asof) && S`The newest data is from ${B(asof)}${pulled.length > 0 && S`; the last successful pull finished at ${pulled}`}. `,
    !known(asof) && pulled.length > 0 && S`The last successful pull finished at ${pulled}. `,
    behind !== null && behind > 0 &&
      S`${B(num(behind))} of them end before ${known(asof) ? "that date" : "the newest"}; a slow series lags by nature.`,
  );
  const { down, st, book } = reach(gi, i);
  const products = [...down.keys()].filter((j) => node(gi, j).k === "product").length;
  const parts: Bit[] = [products > 0 && B(plural(products, "product")), st > 0 && ofDesk(gi, st), book && "the book"].filter(Boolean);
  return {
    title: n.n || n.l,
    paras: [trim(first), parts.length > 0 && S`From here its data reaches ${list(parts)}.`].filter((p): p is Seg[] => Array.isArray(p) && p.length > 0),
    caveats: [],
  };
}

// ------------------------------------------------------------------ the clock
const FINAL = new Set<Run["state"]>(["done", "failed", "cancelled"]);
const DAY_MS = 24 * 60 * 60 * 1000;
const since = (v: string): number => Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/.test(v) ? v : `${v}Z`);

/** A job's runs in `ctx.runs`, by state (ruling 17). A finished run counts when it started in the day before `now`, or its start is not yet known. */
function runsOf(kind: string, ctx: ReadingCtx): { ended: Run[]; running: Run[]; queued: Run[] } {
  const mine = ctx.runs.filter((r) => r.kind === kind);
  const from = ctx.now.getTime() - DAY_MS;
  return {
    ended: mine.filter((r) => FINAL.has(r.state) && !(r.start !== null && since(r.start) < from)),
    running: mine.filter((r) => r.state === "running"),
    queued: mine.filter((r) => r.state === "queued"),
  };
}

function job(gi: GraphIndex, i: number, n: MNode, ctx: ReadingCtx): Reading {
  const m = n.m;
  const sched = schedule(m);
  const first = segs(sched.length > 0 && S`It runs ${sched}, New York time.`,
    m.heavy === true && " It is marked heavy, so it never starts during the market session.");

  const { ended, running, queued } = runsOf(str(m.kind) ?? n.id.replace(/^j:/, ""), ctx);
  const failed = ended.filter((r) => r.state === "failed").length, cancelled = ended.filter((r) => r.state === "cancelled").length;
  const cpus = ended.map((r) => fin(r.cpu)).filter((c): c is number => c !== null);
  const how = (k: number, what: string): Bit => k > 0 && (ended.length === 1 ? `, which ${what}` : [", ", B(num(k)), ` of which ${what}`]);
  const progress = fin(running[0]?.progress);
  const runs = segs(
    ended.length > 0
      ? S`In the last day it ran ${B(ended.length === 1 ? "once" : `${num(ended.length)} times`)}${how(failed, "failed")}${how(cancelled, cancelled > 1 ? "were cancelled" : "was cancelled")}${
        cpus.length > 0 && S`${failed + cancelled > 0 && ","} for ${B(num(cpus.reduce((a, c) => a + c, 0)))} CPU-seconds`}. `
      : running.length === 0 && "It has no run in the last day. ",
    running.length > 0 && S`It is running now${progress !== null && S`, ${B(pct(progress, 0))} through`}. `,
    queued.length > 0 && (queued.length === 1 ? "A run is queued." : S`${B(num(queued.length))} runs are queued.`),
  );

  const feeds = ups(gi, i, "feed");
  const inputs = gi.up[i].filter((j) => node(gi, j).k !== "feed");
  const series = inputs.filter((j) => node(gi, j).k === "series").length;
  const named = inputs.filter((j) => node(gi, j).k !== "series").map((j) => node(gi, j).k === "product" ? S`the ${refAt(gi, j)}` : refAt(gi, j));
  const written = downs(gi, i, "series").length;
  const published = downs(gi, i, "product");
  const wiring = segs(
    feeds.length > 0 && S`It pulls the ${list(feeds.map((j) => refAt(gi, j)))} ${feeds.length === 1 ? "feed" : "feeds"}`,
    feeds.length > 0 && (written > 0 ? S` and writes ${B(plural(written, "series", "series"))}. ` : ". "),
    !feeds.length && written > 0 && S`It writes ${B(plural(written, "series", "series"))}. `,
    (series > 0 || named.length > 0) && S`It reads ${list([series > 0 && B(plural(series, "series", "series")), ...named].filter(Boolean))} directly. `,
    published.length > 0 && S`It publishes the ${list(published.map((j) => refAt(gi, j)))}. `,
    reaches(gi, i, "Downstream it"),
  );
  return { title: n.l || n.n, paras: [first, runs, wiring].map(trim).filter((p) => p.length > 0), caveats: [] };
}

/** A paragraph without the trailing space its last sentence left. */
function trim(p: Seg[]): Seg[] {
  const last = p[p.length - 1];
  return last && isText(last) ? [...p.slice(0, -1), ...segs(last.t.trimEnd())] : p;
}

// ------------------------------------------------------------------ products
const LEVERAGE = (s: string): boolean => /\bleverage\b/i.test(s) && /\bverdict\b/i.test(s);
const FILLS = (s: string): boolean => /\bfills?\b/i.test(s) && /\bclose\b/i.test(s) && /\bopen\b/i.test(s);

/** The kind-specific paragraphs and caveats of a product with a manifest. */
type Body = { paras: Seg[][]; caveats: string[] };
type ProductReading = (gi: GraphIndex, i: number, m: Record<string, unknown>, notes: string[]) => Body;

const lensWhy = (m: Record<string, unknown>, what: string): Seg[] =>
  str(m.lens_why) ? S`${what} could not be read, so its figures are not given here: ${str(m.lens_why)}.` : [];

const farm: ProductReading = (gi, i, m, notes) => {
  const lens = obj(m.lens), board = rows(lens.board);
  const total = gi.down[i].filter((j) => node(gi, j).id.startsWith("c:")).length;
  const withResult = fin(lens.cells), trials = fin(lens.trials);
  let first: Seg[];
  if (Array.isArray(lens.board)) {
    const best = board.filter((r) => fin(r[2]) !== null).sort((a, b) => (b[2] as number) - (a[2] as number))[0];
    const pass = board.filter((r) => r[1] === "PASS").length;
    first = segs(
      withResult !== null && S`Its latest run has ${B(plural(withResult, "cell"))} with a result${total > withResult && S` out of ${B(num(total))}`}${
        trials !== null && S`, and every DSR here is deflated by all ${B(num(trials))} trials in the farm`}. `,
      best && str(best[0]) && S`The best out-of-sample Sharpe is ${B(num(best[2] as number, 2))}, from ${ref(gi, `c:${best[0]}`, str(best[0])!)}${
        fin(best[3]) !== null && S`, with a DSR of ${B(num(best[3] as number, 2))}`}. `,
      board.length > 0 && (pass === 0 ? "No cell passes." : S`${B(num(pass))} of the ${num(board.length)} cells ${pass === 1 ? "passes" : "pass"} every gate.`),
    );
  } else {
    first = segs(total > 0 && S`Its latest run has ${B(plural(total, "cell"))}. `, lensWhy(m, "Its leaderboard"));
  }
  const st = gi.out.nodes.map((_, j) => j).filter((j) => node(gi, j).k === "strategy" && ups(gi, j, "result").some((c) => node(gi, c).id.startsWith("c:")));
  const caps = st.map((j) => fin(node(gi, j).m.capital)).filter((c): c is number => c !== null);
  const desk = st.length > 0 &&
    S`${ofDesk(gi, st.length)} ${st.length === 1 ? "is a farm cell" : "are farm cells"}${caps.length > 0 && [", at ", shares(caps), " of capital", st.length > 1 && " each"]}.`;
  return { paras: [trim(first), desk || []], caveats: notes };
};

const daily: ProductReading = (_gi, _i, _m, notes) => {
  const c = parseDailyNote(notes);
  const rest = notes.slice(1);
  const lev = rest.find(LEVERAGE), fill = rest.find((s) => s !== lev && FILLS(s));
  const caveats = [lev, fill].filter((s): s is string => s !== undefined);
  const counts = c && segs(
    S`In that run it wrote ${B(plural(c.written, "new document"))}`,
    c.again > 0 ? S` and ${B(num(c.again))} again, because the desk had not sized the last one. ` : ". ",
    S`It held ${B(num(c.held))} with no new decision and refused ${B(num(c.refused))}.`,
    c.late > 0 && S` It wrote ${B(num(c.late))} late, after a period ended with no document.`,
    c.present > 0 && S` ${B(num(c.present))} ${c.present === 1 ? "was" : "were"} already there for the session.`,
  );
  return {
    paras: [
      segs("This is where research becomes orders: it writes each farm strategy's signal document for the desk to size.", counts && [" ", counts]),
      caveats.length > 0 ? S`It notes ${B(num(caveats.length))} ${caveats.length === 1 ? "way" : "ways"} the farm's tests differ from what the desk trades.` : [],
    ],
    caveats,
  };
};

const overlay: ProductReading = (gi, _i, _m, notes) => {
  const o = parseOverlayNote(notes), fallback = parseOverlayFallback(notes);
  const lead = "One number for the whole desk: an exposure scale toward a volatility target.";
  if (!o) return { paras: [segs(lead, notes.length > 0 && " Its latest note could not be read as a scale, so it is quoted as written.")], caveats: notes.slice(0, 1) };
  const est = o.estimator && ref(gi, `ce:${o.estimator}`, o.estimator);
  const sessions = o.sessions !== null && S` over ${B(num(o.sessions))} sessions`;
  const unused = notes.some((s) => s.includes("exposure_scale") && /reported, never used/.test(s));
  return {
    paras: [segs(
      lead,
      S` On its latest run the combined book forecast ${B(pct(o.forecast, 1))} volatility against a ${B(share(o.target))} target, so the scale is ${B(num(o.scale, 2))}${
        o.scale >= 1 && ", the most it can be"}.`,
      est && (fallback === o.estimator
        ? S` The covariance league gave no winner it could use, so it fell back to ${est}${sessions}.`
        : S` It estimates covariance with ${est}${sessions}.`),
      unused && " The regime model's probability and the Monte Carlo VaR are reported beside it, and never used.",
    )],
    caveats: [],
  };
};

const volLeague: ProductReading = (gi, _i, m, notes) => {
  const models = rows(obj(m.lens).models)
    .filter((r) => str(r[0]) && fin(r[1]) !== null)
    .sort((a, b) => (b[1] as number) - (a[1] as number));
  const names = new Set(models.map((r) => fin(r[3])));
  const same = names.size === 1 && fin(models[0]?.[3]) !== null;
  const item = (r: unknown[], k: number): Seg[] => S`${ref(gi, `lm:${r[0]}`, String(r[0]).toUpperCase())}${k === 0 ? " stays in the statistical best set" : ""} for ${B(pct(r[1] as number, 1))}${same && k === 0 && " of them"}${
    !same && fin(r[3]) !== null && S` of its ${B(num(r[3] as number))} names`}`;
  const body = models.length > 0 && S`${same && S`Scored across ${B(num(models[0][3] as number))} names, `}${list(models.map(item))}.`;
  return { paras: [body || lensWhy(m, "Its model table")], caveats: notes };
};

const covLeague: ProductReading = (gi, _i, m, notes) => {
  const wins = rows(obj(m.lens).estimators).filter((r) => r[3] === 1 && str(r[0]) && str(r[1]) && fin(r[2]) !== null);
  const body = wins.length > 0 && S`The winner on each universe: ${list(wins.map((r, k) =>
    S`${ref(gi, `ce:${r[0]}`, String(r[0]))} on ${String(r[1])} (${B(pct(r[2] as number, 1))}${k === 0 && " realized volatility"})`))}.`;
  return { paras: [body || lensWhy(m, "Its league table")], caveats: notes };
};

const METHOD: Record<string, string> = { fhs: "FHS", copula_t: "t-copula" };
const atlas: ProductReading = (gi, _i, m, notes) => {
  const all = rows(obj(m.lens).measures).filter((r) => str(r[0]) && fin(r[1]) !== null && fin(r[2]) !== null && fin(r[3]) !== null);
  const h = all.some((r) => r[1] === 10) ? 10 : Math.max(...all.map((r) => r[1] as number));
  const body = all.filter((r) => r[1] === h).map((r) => {
    const id = `am:${r[0]}:${h}`;
    return S`${gi.byId.has(id) ? ref(gi, id, node(gi, gi.byId.get(id)!).n) : `${METHOD[r[0] as string] ?? r[0]} ${num(h)}-day VaR and ES`} are ${B(pct(r[2] as number, 2))} and ${
      B(pct(r[3] as number, 2))}${fin(r[4]) !== null && S`, from ${B(num(r[4] as number))} paths`}.`;
  });
  return { paras: [body.length > 0 ? segs(...body.map((b, k) => [k ? " " : "", b])) : lensWhy(m, "Its measures table")], caveats: notes };
};

const xs: ProductReading = (_gi, _i, m, notes) => {
  const d = rows(obj(m.lens).deciles).filter((r) => fin(r[0]) !== null && fin(r[1]) !== null).sort((a, b) => (a[0] as number) - (b[0] as number));
  const r = d.map((x) => x[1] as number);
  const climbs = r.every((x, k) => k === 0 || x >= r[k - 1]);
  const body = d.length > 1 && S`On its holdout, the top decile by score had a mean next-month return of ${B(pct(r[r.length - 1], 2))} and the bottom decile ${B(pct(r[0], 2))}. Returns ${
    climbs ? "climb decile by decile" : "do not climb decile by decile"}.`;
  return { paras: [body || lensWhy(m, "Its decile table")], caveats: notes };
};

const regime: ProductReading = (_gi, _i, m, notes) => {
  const lens = obj(m.lens), p = fin(lens.p_high), day = nyDay(str(lens.date)), weeks = fin(lens.weeks);
  const body = p !== null && S`${known(day) && S`As of ${B(day)}, `}it puts the probability of the high-volatility state at ${B(num(p, 2))}${
    weeks !== null && S`, from ${B(num(weeks))} weeks of data`}.`;
  return { paras: [body ? capital(body) : lensWhy(m, "Its probabilities")], caveats: notes };
};

const surface: ProductReading = (gi, _i, m, notes) => {
  const lens = obj(m.lens), name = str(lens.underlying), atm = fin(lens.atm30), slices = fin(lens.slices), names = fin(lens.names), day = nyDay(str(lens.asof));
  const body = segs(
    names !== null && S`It keeps a volatility-surface history for ${B(num(names))} underlyings. `,
    name && atm !== null && S`${known(day) && S`On ${B(day)}, `}${ref(gi, `d:${name}`, name)}'s one-month at-the-money implied volatility was ${B(pct(atm, 1))}${
      slices !== null && S`, from ${B(plural(slices, "expiry slice"))}`}.`,
  );
  return { paras: [body.length > 0 ? trim(capital(body)) : lensWhy(m, "Its history")], caveats: notes };
};

/** The paragraph with its first letter capitalised. */
function capital(p: Seg[]): Seg[] {
  const [f, ...rest] = p;
  return f && isText(f) ? [{ t: f.t.charAt(0).toUpperCase() + f.t.slice(1) }, ...rest] : p;
}

const BY_KIND: Record<string, ProductReading> = {
  "farm.sweep": farm, "signals.daily": daily, "signals.overlay": overlay, "vol.forecast_league": volLeague, "cov.league": covLeague,
  "risk.mc_atlas": atlas, "models.xs_lgbm": xs, "regime.hmm": regime, "vol.surface_history": surface,
};

function product(gi: GraphIndex, i: number, n: MNode): Reading {
  const m = n.m, title = n.n || n.l;
  if (n.id === "p:research") return research(gi, i, n);
  const kind = str(m.kind) ?? n.id.replace(/^p:/, "");
  const stale = m.stale === true && "That artifact is past its schedule's max age.";
  if (kind === "desk.report") {
    // spec §7 Q1: only its run times; its contents are not rendered on this page
    const done = at(m.finished);
    return {
      title,
      paras: [segs(done.length > 0 && S`Its latest report finished at ${done}. `, stale), segs("What it reports stays on the research pages; this page shows only when it ran.")]
        .map(trim).filter((p) => p.length > 0),
      caveats: [],
    };
  }
  const verdict = str(m.verdict), detail = str(m.detail), done = at(m.finished), asof = nyDay(str(m.asof));
  const head = segs(
    verdict && S`Verdict: ${B(verdict)}${detail && ` (${detail})`}. `,
    done.length > 0 && S`Its latest artifact finished at ${done}${known(asof) && S`, on data as of ${B(asof)}`}. `,
    stale,
  );
  const notes = strs(m.notes);
  const body = (BY_KIND[kind] ?? ((): Body => ({ paras: [], caveats: notes })))(gi, i, m, notes);
  const tail = segs(directReaders(gi, i).length > 0 && [readers(gi, i), " "], reaches(gi, i, "Downstream it"));
  return { title, paras: [head, ...body.paras, tail].map(trim).filter((p) => p.length > 0), caveats: body.caveats };
}

function research(gi: GraphIndex, i: number, n: MNode): Reading {
  const st = downs(gi, i, "strategy");
  const k = fin(n.m.strategies) ?? st.length;
  return {
    title: n.n || n.l,
    paras: [S`The research service sends signals for ${ofDesk(gi, k)}${st.length > 0 && st.length <= 8 && [": ", list(st.map((j) => refAt(gi, j)))]}.`],
    caveats: [],
  };
}

// ------------------------------------------------------------------ results and strategies
function result(gi: GraphIndex, i: number, n: MNode): Reading {
  const m = n.m, title = n.n || n.l;
  const inside = ups(gi, i, "product")[0];
  if (n.id.startsWith("c:")) return cell(gi, i, n);
  let body: Seg[] = [];
  if (n.id.startsWith("lm:") && fin(m.mcs_rate) !== null) {
    body = S`It stays in the statistical best set for ${B(pct(m.mcs_rate as number, 1))}${fin(m.names) !== null && S` of ${B(num(m.names as number))} names`}${
      fin(m.mean_rank) !== null && S`, with a mean rank of ${B(num(m.mean_rank as number, 2))}`}.`;
  } else if (n.id.startsWith("ce:")) {
    const by = Object.entries(m).map(([u, v]) => [u, fin(obj(v).vol), fin(obj(v).rank)] as const).filter(([, v]) => v !== null);
    if (by.length) body = S`Its realized volatility by universe: ${list(by.map(([u, v, r]) => S`${u} ${B(pct(v!, 1))}${r !== null && S` (rank ${B(num(r))})`}`))}.`;
  } else if (n.id.startsWith("am:") && fin(m.var) !== null && fin(m.es) !== null) {
    const usd = fin(m.var_usd) !== null && fin(m.es_usd) !== null && S`, or ${B(`$${num(m.var_usd as number)}`)} and ${B(`$${num(m.es_usd as number)}`)} on the atlas's book`;
    body = S`The VaR is ${B(pct(m.var as number, 2))} and the ES ${B(pct(m.es as number, 2))}${fin(m.paths) !== null && S`, from ${B(num(m.paths as number))} paths`}${usd}.`;
  }
  const where = inside !== undefined && S`A result inside the ${refAt(gi, inside)}.`;
  return { title, paras: [where || [], body].filter((p) => p.length > 0), caveats: [] };
}

function cell(gi: GraphIndex, i: number, n: MNode): Reading {
  const m = n.m, title = n.n || n.l;
  const farmLens = ups(gi, i, "product").map((j) => obj(node(gi, j).m.lens)).find((l) => fin(l.trials) !== null);
  const trials = farmLens ? fin(farmLens.trials) : null;
  const st = downs(gi, i, "strategy")[0];
  const cap = st === undefined ? null : fin(node(gi, st).m.capital);
  const becomes = st !== undefined && S`On the desk it trades as ${refAt(gi, st)}${cap !== null && S`, at ${B(share(cap))} of capital`}.`;
  if (str(m.status) && m.status !== "ok") {
    return { title, paras: [S`This cell has no result from the latest run${str(m.reason) && `: ${str(m.reason)}`}.`, becomes || []].filter((p) => p.length > 0), caveats: [] };
  }
  const combos = fin(m.combos) ?? fin(m.n_combos), sharpe = fin(m.oos_sharpe_ann), psr = fin(m.psr), dsr = fin(m.dsr_farm), pbo = fin(m.pbo);
  const verdict = str(m.verdict), detail = str(m.detail);
  const figures = segs(
    combos !== null && S`It tested ${B(plural(combos, "parameter variant"))}. `,
    sharpe !== null && S`The chosen one has an out-of-sample Sharpe of ${B(num(sharpe, 2))}${psr !== null && S` and a probabilistic Sharpe (PSR) of ${B(num(psr, 2))}`}. `,
    dsr !== null && S`${trials !== null ? S`Deflated by the farm's ${B(num(trials))} trials, its` : "Its"} deflated Sharpe (DSR) is ${B(num(dsr, 2))}. `,
    pbo !== null && S`Its probability of backtest overfitting (PBO) is ${B(num(pbo, 2))}. `,
    verdict && S`Verdict: ${B(verdict)}${detail && ` (${detail})`}.`,
  );
  return { title, paras: [trim(figures), becomes || []].filter((p) => p.length > 0), caveats: [] };
}

function strategy(gi: GraphIndex, i: number, n: MNode): Reading {
  const m = n.m, cap = fin(m.capital), symbols = strs(m.symbols), producer = str(m.producer);
  const cellIn = ups(gi, i, "result")[0];
  const from = producer === "signals.daily"
    ? S`Its signals come from ${ref(gi, "p:signals.daily", "Daily signals")}${cellIn !== undefined && S`, using the farm cell ${refAt(gi, cellIn)}`}.`
    : producer === "research-service" ? S`Its signals come from the ${ref(gi, "p:research", "research service")}.`
    : producer && S`Its signals come from ${producer}.`;
  return {
    title: n.n || n.l,
    paras: [segs(
      S`One of the desk's ${B(num(strategyCount(gi)))} strategies${cap !== null && S`, with ${B(share(cap))} of capital`}.`,
      symbols.length > 0 && S` It trades ${list(symbols.map((s) => ref(gi, `d:${s}`, s)))}.`,
      from && [" ", from],
    )],
    caveats: [],
  };
}

// ------------------------------------------------------------------ the desk
function book(gi: GraphIndex, i: number, n: MNode): Reading {
  const m = n.m;
  const st = ups(gi, i, "strategy");
  const k = fin(m.strategies) ?? st.length;
  const sources = new Map<number, number>();
  for (const s of st) for (const p of ups(gi, s, "product")) sources.set(p, (sources.get(p) ?? 0) + 1);
  const capital = fin(m.capital), cap = fin(m.incubation_cap), target = fin(m.vol_target);
  const overlay = ups(gi, i, "product").find((j) => node(gi, j).id === "p:signals.overlay");
  const first = segs(
    S`${B(plural(k, "strategy", "strategies"))} send${k === 1 ? "s" : ""} it signals${sources.size > 0 && [", ", list([...sources].map(([p, c]) => S`${B(num(c))} from ${refAt(gi, p)}`))]}.`,
    capital !== null && S` Together they hold ${B(share(capital))} of capital${cap !== null && S`, under a ${B(share(cap))} incubation cap`}.`,
    overlay !== undefined && S` The ${refAt(gi, overlay, "exposure overlay")} scales its exposure${target !== null && S` toward a ${B(share(target))} volatility target`}.`,
  );
  const why = str(m.summary_why);
  if (why) return { title: n.n || n.l, paras: [first, S`Its limits, kill switch and position count are not shown here. The system's reason: “${why}”.`], caveats: [] };

  const asof = at(m.summary_asof), positions = fin(m.positions), kill = str(m.kill), limits = ups(gi, i, "limit");
  const lim = fin(m.limits) ?? limits.length;
  const killAt = gi.byId.get("kill");
  const second = segs(
    positions !== null && S`${asof.length > 0 ? S`As of ${asof} it` : "It"} held ${B(plural(positions, "position"))}. `,
    lim === 0 ? "No limit bounds it. " : S`${B(plural(lim, "limit"))} ${lim === 1 ? "bounds" : "bound"} it${limits.length > 0 && [": ", list(limits.map((j) => refAt(gi, j)))]}. `,
    kill && S`Its ${killAt !== undefined ? ref(gi, "kill", "kill switch") : "kill switch"} is ${B(kill)}.`,
  );
  return { title: n.n || n.l, paras: [first, trim(second)].filter((p) => p.length > 0), caveats: [] };
}

/** When the book's summary is from, for a limit or the kill switch: ", as of 4:30 PM on 28 Oct". */
function summaryAt(gi: GraphIndex): Seg[] {
  const b = gi.byId.get("book");
  return b === undefined ? [] : at(node(gi, b).m.summary_asof);
}

function limit(gi: GraphIndex, n: MNode): Reading {
  const m = n.m, kind = str(m.kind), scope = str(m.scope);
  const on = !scope ? null
    : scope === "portfolio" ? "the whole portfolio"
    : scope.startsWith("instrument:") ? S`${ref(gi, `d:${scope.slice(11).toUpperCase()}`, scope.slice(11).toUpperCase())} alone`
    : scope.startsWith("sector:") ? `the sector ${scope.slice(7)}` : scope;
  const killAt = gi.byId.get("kill");
  const halts = killAt !== undefined && gi.edgeAt.has(`${killAt}>${gi.byId.get("book")}`);
  const when = summaryAt(gi);
  return {
    title: n.l || n.n,
    paras: [segs(
      kind && S`It is a ${kind.replace(/_/g, " ")} limit${on && [" on ", on]}. `,
      m.trips === true && S`If it breaks, it trips the ${ref(gi, "kill", "kill switch")}${halts && ", which halts the book"}. `,
      m.trips === false && "It does not trip the kill switch. ",
      when.length > 0 && S`This is as of ${when}, from the desk report's book summary. `,
      "Its threshold is not public.",
    )],
    caveats: [],
  };
}

function killSwitch(gi: GraphIndex, n: MNode): Reading {
  const m = n.m, state = str(m.state), when = summaryAt(gi);
  const trips = Array.isArray(m.trips_on) ? strs(m.trips_on) : null;
  const names = trips && list(trips.map((t) => ref(gi, `lim:${t}`, t)));
  return {
    title: "Kill switch",
    paras: [segs(
      state ? S`The kill switch is ${B(state)}${when.length > 0 && S`, as of ${when}`}. ` : "Its state could not be read. ",
      trips && trips.length === 0 && "No limit trips it.",
      trips && trips.length > 0 && (state === "armed" ? S`If ${names} ${trips.length === 1 ? "breaks" : "break"}, it halts the book.` : S`${names} ${trips.length === 1 ? "trips" : "trip"} it.`),
    )].map(trim),
    caveats: [],
  };
}

// ------------------------------------------------------------------ the reading
/** What node `i` is and what it did, in plain words, from the response alone. A node that is not one reads as nothing. */
export function reading(gi: GraphIndex, i: number, ctx: ReadingCtx): Reading {
  if (!Number.isInteger(i) || i < 0 || i >= gi.out.nodes.length) return { title: "", paras: [], caveats: [] };
  const n = node(gi, i);
  if (n.status === "missing") {
    const why = str(n.why);
    return { title: n.n || n.l, paras: [why ? S`This is missing from the machine: ${why}.` : segs("This is missing from the machine.")], caveats: [] };
  }
  switch (n.k) {
    case "series": return n.m.track === "daily" ? ticker(gi, i, n) : series(gi, i, n);
    case "feed": return feed(gi, i, n);
    case "job": return job(gi, i, n, ctx);
    case "product": return product(gi, i, n);
    case "result": return result(gi, i, n);
    case "strategy": return strategy(gi, i, n);
    case "book": return book(gi, i, n);
    case "limit": return limit(gi, n);
    case "kill": return killSwitch(gi, n);
    default: return { title: n.n || n.l || n.id, paras: [], caveats: [] };
  }
}
