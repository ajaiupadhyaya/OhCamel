/**
 * The tour (spec §6.8): eight steps that walk a first-time visitor inward, from the world's data to the book.
 * Each selects one real thing and says in plain words what it is and did. Pure: the graph and the runs in,
 * text segments out, for React to render as text, never as HTML.
 *
 * Every figure is read from the response when the step is shown. The approved prototype's STEPS are the
 * wording reference, never the numbers. A step whose subject is not in the graph, is missing, or is past its
 * schedule's max age says so, and gives none of that subject's figures.
 */
import { heaviest, runningRuns } from "./day";
import { dur, instant, num, nyTime, nyWeekday, pct, plural } from "./fmt";
import { parseOverlayNote } from "./notes";
import {
  at, B, capital, cellRef, fin, inputs, known, list, node, obj, ofDesk, paras, ranInLastDay, reach, reason, ref, refAt, rows, S, schedule, segs, share,
  shares, sideRoute, str, strategyCount, strs, ups,
} from "./prose";
import type { ReadingCtx, Seg } from "./prose";
import { reading } from "./readings";
import type { StepView } from "./url";
import { countKinds, directReaders } from "./web";
import type { GraphIndex } from "./web";

/**
 * Where a step puts the camera. "overview" and "yours" are fixed views of the whole machine (the prototype's
 * `[target (0, 1.4, 0), sph(27, 0.92, 0.35)]` and `[(0, 1.2, 0), sph(23, 0.62, −0.5)]`); "node" frames the
 * step's selection, and when a step selects nothing (its subject is not in the graph) the scene keeps the
 * whole machine in view.
 */
export type TourCam = "overview" | "node" | "yours";

export interface TourStep {
  /** The step's name, stable across payloads. */
  key: string;
  /** The node the step selects: null by design (the first and last), or when its subject is not in the graph. */
  sel(gi: GraphIndex, ctx: ReadingCtx): number | null;
  cam: TourCam;
  title(gi: GraphIndex, ctx: ReadingCtx): string;
  /** Paragraphs of plain text, figures and links to nodes that exist. */
  body(gi: GraphIndex, ctx: ReadingCtx): Seg[][];
}

// ------------------------------------------------------------------ subjects
/** What a step is about: its node, and how the step names it ("the" and "strategy farm"). */
interface Subject {
  id: string;
  the: boolean;
  name: string;
}
const PRICES: Subject = { id: "feed:prices", the: true, name: "price feed" };
const NVDA: Subject = { id: "d:NVDA", the: false, name: "NVDA" };
const FARM: Subject = { id: "p:farm.sweep", the: true, name: "strategy farm" };
const OVERLAY: Subject = { id: "p:signals.overlay", the: true, name: "exposure overlay" };
const BOOK: Subject = { id: "book", the: true, name: "book" };

const indexOf = (gi: GraphIndex, s: Subject): number | null => gi.byId.get(s.id) ?? null;

/**
 * What a step says in place of its subject's figures when the subject cannot vouch for them: it is not in the
 * graph, it is missing (with the system's own reason), or its latest artifact is past its schedule's max age.
 * Null when the subject is there and current.
 */
function unavailable(gi: GraphIndex, s: Subject): Seg[] | null {
  const i = gi.byId.get(s.id);
  const who = capital(segs(s.the && "the ", ref(gi, s.id, s.name)));
  if (i === undefined) return S`${who} is not in the machine.`;
  const n = node(gi, i);
  if (n.status === "missing") {
    const why = str(n.why);
    return S`${who} is missing from the machine${why && [": ", reason(gi, why)]}.`;
  }
  if (n.m.stale === true) {
    const done = at(n.m.finished);
    return S`${who}'s latest artifact${done.length > 0 && S`, finished at ${done},`} is past its schedule's max age, so its figures are not given here. The Reading tab gives them with that warning.`;
  }
  return null;
}

/** A job by the kind its runs carry, linked ("the Strategy farm"), or the kind in words when the graph has no such job. */
function jobNamed(gi: GraphIndex, kind: string): Seg[] {
  const j = gi.byId.get(`j:${kind}`);
  return j === undefined ? S`the ${kind} job` : S`the ${refAt(gi, j)}`;
}

// ------------------------------------------------------------------ 1. the overview
const overview: TourStep = {
  key: "overview",
  cam: "overview",
  sel: () => null,
  title: (gi) => {
    const t = nyTime(gi.out.asof), d = nyWeekday(gi.out.asof);
    return known(t) && known(d) ? `The machine at ${t} on a ${d}` : "The machine";
  },
  body: (gi) => {
    const st = strategyCount(gi), book = gi.byId.has("book");
    return paras(
      segs(
        S`This is OhCamel drawn as its dependency graph${book && ": rings that narrow to one book"}. `,
        "The outer rings are the world's data. The middle ring is a clock, the machine's day, with every scheduled job at its time. ",
        S`Inside the clock are what the jobs publish${st > 0 && S`, the desk's ${B(plural(st, "strategy", "strategies"))}`}${
          book && S` and, at the center, the ${ref(gi, "book", "paper account")} the desk trades`}.`,
      ),
      segs("Every line is a real dependency, read from the system's own schedules and provenance. Scroll to walk inward, or click anything to take over."),
    );
  },
};

// ------------------------------------------------------------------ 2. the price feed
/** The warehouse's tracks, in the order the step names them, each with how it counts its series. */
const TRACKS: [string, (k: number) => Seg[]][] = [
  ["daily", (k) => S`daily bars for ${B(plural(k, "ticker"))}`],
  ["minute", (k) => S`one-minute bars for ${B(plural(k, "name"))}`],
  ["options", (k) => S`option chains for ${B(plural(k, "name"))}`],
  ["macro", (k) => [B(plural(k, "FRED series", "FRED series"))]],
  ["factors", (k) => [B(plural(k, "factor series", "factor series"))]],
  ["filings", (k) => S`SEC filings for ${B(plural(k, "company", "companies"))}`],
  ["13f", (k) => S`the 13F holdings of ${B(plural(k, "fund manager"))}`],
];

const prices: TourStep = {
  key: "prices",
  cam: "node",
  sel: (gi) => indexOf(gi, PRICES),
  title: () => "Outside: the world's data",
  body: (gi) => {
    const off = unavailable(gi, PRICES);
    const f = gi.byId.get(PRICES.id), j = gi.byId.get("j:ingest.bars_daily");
    // the pull is said only as the graph draws it: this feed, its edge into the daily job, and that job present
    const pulls = !off && f !== undefined && j !== undefined && gi.edgeAt.has(`${f}>${j}`) && node(gi, j).status !== "missing";
    const when = pulls ? schedule(node(gi, j).m) : [];
    const pull = pulls && capital(S`${when.length > 0 && [when, ", New York time, "]}the ${refAt(gi, j)} job pulls this feed's daily bars into the warehouse.`);

    const series = gi.out.nodes.filter((n) => n.k === "series");
    const held = TRACKS.map(([track, say]) => [series.filter((n) => n.m.track === track).length, say] as const)
      .filter(([k]) => k > 0)
      .map(([k, say]) => say(k));
    return paras(
      off ?? pull,
      held.length > 0 && S`The rings just inside the feeds are the warehouse's tracks: ${list(held)}. That is ${B(plural(series.length, "series", "series"))} in all.`,
    );
  },
};

// ------------------------------------------------------------------ 3. one stock
const nvda: TourStep = {
  key: "nvda",
  cam: "node",
  sel: (gi) => indexOf(gi, NVDA),
  title: () => "One stock, followed inward",
  body: (gi) => {
    const click = "Click any ticker on the outer rings to see its own web.";
    const off = unavailable(gi, NVDA);
    if (off) return paras(segs(off, " ", click));
    const i = gi.byId.get(NVDA.id)!, l = node(gi, i).l || "NVDA";
    const track = (p: string, word: string): Seg | false => gi.byId.has(p + l) && ref(gi, p + l, word);
    const keeps = S`The warehouse keeps ${refAt(gi, i)}'s ${list(["daily bars", track("m:", "one-minute bars"), track("o:", "option chain"), track("s:", "SEC filings")])}.`;

    // where its other tracks go, said only where an edge goes there (the minute bars to the vol league, the chain to the surface job)
    const onward = (p: string, what: string): Seg[] | false => {
      const k = gi.byId.get(p + l);
      const r = k === undefined ? [] : directReaders(gi, k);
      return r.length > 0 && S`its ${what} ${list(r.map((j) => S`the ${refAt(gi, j)}`))}`;
    };
    const r = directReaders(gi, i);
    const further = [onward("m:", "one-minute bars feed"), onward("o:", "option chain feeds")];
    const read = segs(
      r.length > 0 ? S`Its daily bars are read directly by ${B(plural(r.length, "job"))}` : "No job reads its daily bars directly",
      further.some(Boolean) && ["; ", list(further)],
      ".",
    );

    const { down, st, book } = reach(gi, i);
    const route = st > 0 ? S`It reaches ${list([ofDesk(gi, st), book && "the book"])}.` : book ? sideRoute(gi, i, down) : segs("Nothing it feeds reaches the book.");
    return paras(segs(keeps, " ", read), segs("The cyan lines are where its data goes. ", route, " ", click));
  },
};

// ------------------------------------------------------------------ 4. the clock
const clock: TourStep = {
  key: "clock",
  cam: "node",
  sel: (gi, ctx) => {
    const top = heaviest(ctx.runs, ctx.now);
    return top === null ? null : (gi.byId.get(`j:${top.kind}`) ?? null);
  },
  title: () => "The clock",
  body: (gi, ctx) => {
    const jobs = countKinds(gi, gi.out.nodes.keys()).job;
    const dial = segs(
      S`The clock is a dial of the day in New York time${jobs > 0 && S`, with ${B(plural(jobs, "job"))} at ${jobs === 1 ? "its hour" : "their hours"}`}. `,
      "Each tick is a scheduled run, and the amber arc is the market session, when no heavy job may start.",
    );

    // spec §6.8: the heaviest job of the last day is chosen from the runs, never by name
    const top = heaviest(ctx.runs, ctx.now);
    let heavy: Seg[];
    if (top === null) {
      heavy = ctx.runs.some((r) => ranInLastDay(r, ctx.now))
        ? segs("No run in the last day both finished cleanly and recorded its CPU-seconds, so none is singled out as the heaviest.")
        : segs("No job has run in the last day.");
    } else {
      // a run may lack its start or end (ruling 17): then no time or span is given, only what the run has
      const start = instant(top.start), end = instant(top.end), when = at(top.start);
      const span = start !== null && end !== null && end >= start && B(dur((end - start) / 1000));
      const j = gi.byId.get(`j:${top.kind}`);
      const job = j === undefined ? null : node(gi, j);
      const reads = j === undefined ? [] : inputs(gi, j);
      heavy = segs(
        S`The heaviest job of the last day was ${jobNamed(gi, top.kind)}: its run${when.length > 0 && S` at ${when}`} took ${list([span, S`${B(num(top.cpu))} CPU-seconds`])}.`,
        job?.status === "missing"
          ? [" ", unavailable(gi, { id: job.id, the: true, name: "job" })]
          : reads.length > 0 && S` It reads ${reads} directly.`,
      );
    }

    const now = runningRuns(ctx.runs).map((r) => {
      const j = gi.byId.get(`j:${r.kind}`), p = fin(r.progress);
      return S`${j === undefined ? `The ${r.kind} job` : refAt(gi, j)} is running now${p !== null && S`, ${B(pct(p, 0))} through`}. `;
    });
    return paras(dial, heavy, segs(now, ctx.runs.length > 0 && "The day tab lists every run of the last day, with a replay."));
  },
};

// ------------------------------------------------------------------ 5. the farm
const farm: TourStep = {
  key: "farm",
  cam: "node",
  sel: (gi) => indexOf(gi, FARM),
  title: () => "The strategy farm",
  body: (gi) => {
    const off = unavailable(gi, FARM);
    if (off) return paras(off);
    const p = gi.byId.get(FARM.id)!, m = node(gi, p).m, lens = obj(m.lens);
    const job = ups(gi, p, "job")[0];
    const when = job === undefined ? [] : schedule(node(gi, job).m);
    const reruns = capital(S`${when.length > 0 && [when, " "]}the ${ref(gi, FARM.id, FARM.name)} re-runs its backtests.`);
    if (!Array.isArray(lens.board)) {
      const why = str(m.lens_why);
      return paras(segs(reruns, why && S` Its leaderboard could not be read, so its figures are not given here: ${reason(gi, why)}.`));
    }

    const cells = fin(lens.cells), trials = fin(lens.trials), board = rows(lens.board);
    const best = board.filter((r) => str(r[0]) && fin(r[2]) !== null).sort((a, b) => (b[2] as number) - (a[2] as number))[0];
    const pass = board.filter((r) => r[1] === "PASS").length;
    const top = best && S`The best out-of-sample Sharpe is ${B(num(best[2] as number, 2))}, from ${cellRef(gi, best[0] as string)}`;
    let verdict: Seg[] | false = false;
    if (board.length > 0 && pass === 0) {
      verdict = S`${top ? [top, ", but after"] : "After"} deflating for every trial, no cell passes. The failures are shown, not hidden.`;
    } else if (board.length > 0) {
      verdict = S`${top && [top, ". "]}After deflating for every trial, ${B(num(pass))} of the ${num(board.length)} cells ${pass === 1 ? "passes" : "pass"} every gate.`;
    }
    return paras(
      segs(reruns, cells !== null && S` Its latest run has ${B(plural(cells, "cell"))} with a result${trials !== null && S`, and the farm has run ${B(num(trials))} trials in all`}.`),
      verdict,
    );
  },
};

// ------------------------------------------------------------------ 6. the overlay
const overlay: TourStep = {
  key: "overlay",
  cam: "node",
  sel: (gi) => indexOf(gi, OVERLAY),
  title: () => "Where research becomes orders",
  body: (gi, ctx) => {
    const d = gi.byId.get("p:signals.daily");
    const daily = d !== undefined && node(gi, d).status !== "missing" ? d : undefined;
    const writes = daily !== undefined && S`${refAt(gi, daily)} writes each farm strategy's signal document for the desk to size.`;
    // daily signals' caveats are exactly its leverage and fill notes (products.ts), so this names what they say
    const tests = daily !== undefined && reading(gi, daily, ctx).caveats.length > 0 &&
      S`Select ${refAt(gi, daily)} to read, in its own notes, how the farm's tests differ from what the desk trades.`;

    const off = unavailable(gi, OVERLAY);
    if (off) return paras(writes, off, tests);
    const o = gi.byId.get(OVERLAY.id)!, m = node(gi, o).m;
    const job = ups(gi, o, "job")[0];
    const from = job === undefined ? [] : ups(gi, job, "product");
    const reads = from.length > 0 && S` reads ${list(from.map((j) => S`the ${refAt(gi, j)}`))} and`;
    const sets = S`The ${refAt(gi, o, OVERLAY.name)}${reads} sets one exposure scale for the whole desk, toward a volatility target.`;
    const n = parseOverlayNote(strs(m.notes)), done = at(m.finished);
    const scale = n
      ? S`Its latest run${done.length > 0 && S`, at ${done},`} forecast ${n.forecast > 0 ? S`the combined book's volatility at ${B(pct(n.forecast, 1))}` : "no volatility for the combined book"} against a ${
        B(share(n.target))} target, so the scale is ${B(num(n.scale, 2))}${n.scale >= 1 && ", the most it can be"}.`
      : segs("No note from its latest run states the scale, so none is given here.");
    const own = reading(gi, o, ctx).caveats.length > 0 && " The Reading tab quotes the caveats it records about itself.";
    return paras(segs(writes, writes && " ", sets), segs(scale, own), tests);
  },
};

// ------------------------------------------------------------------ 7. the book
const book: TourStep = {
  key: "book",
  cam: "node",
  sel: (gi) => indexOf(gi, BOOK),
  title: () => "The book",
  body: (gi) => {
    const off = unavailable(gi, BOOK);
    if (off) return paras(off);
    const b = gi.byId.get(BOOK.id)!, m = node(gi, b).m;
    const st = ups(gi, b, "strategy");
    const k = fin(m.strategies) ?? st.length;
    const caps = st.map((j) => fin(node(gi, j).m.capital)).filter((c): c is number => c !== null);
    const cap = fin(m.incubation_cap);
    // a share of capital only when every strategy has one; "each" only when they are all the same
    const each = k > 0 && caps.length === k && [" at ", shares(caps), " of capital", k > 1 && new Set(caps).size === 1 && " each"];
    const first = k > 0
      ? S`Everything ends here: ${B(plural(k, "strategy", "strategies"))}${each}${cap !== null && S`, under a ${B(share(cap))} incubation cap`}.`
      : segs("Everything ends here, though no strategy sends it signals yet.");

    // the live book (spec §7 Q1) only from a book summary, as of its time: counts and states, never thresholds, names of positions or sizes
    let live: Seg[] | false = false;
    if (str(m.summary_asof)) {
      const asof = at(m.summary_asof), positions = fin(m.positions), kill = str(m.kill);
      const lim = fin(m.limits) ?? ups(gi, b, "limit").length;
      const ks = gi.byId.get("kill");
      const trips = ks === undefined ? [] : strs(node(gi, ks).m.trips_on);
      live = segs(
        S`${asof.length > 0 ? S`As of ${asof}, the` : "The"} desk report's book summary shows ${list([
          positions !== null && B(plural(positions, "position")),
          lim === 0 ? "no limits" : B(plural(lim, "limit")),
          kill && S`the ${ref(gi, "kill", "kill switch")} ${B(kill)}`,
        ])}.`,
        kill === "armed" && trips.length > 0 &&
          S` If ${list(trips.map((t) => ref(gi, `lim:${t}`, t)))} ${trips.length === 1 ? "breaks" : "break"}, the kill switch halts the book.`,
      );
    } else if (str(m.summary_why)) {
      live = S`Its limits, kill switch and position count are not shown here. The system's reason: “${reason(gi, str(m.summary_why)!)}”.`;
    }
    return paras(first, live);
  },
};

// ------------------------------------------------------------------ 8. now it's yours
/** The farm cell with the best out-of-sample Sharpe, or null when no cell has one. */
function bestCell(gi: GraphIndex): string | null {
  let best: string | null = null, most = -Infinity;
  for (const n of gi.out.nodes) {
    const s = fin(n.m.oos_sharpe_ann);
    if (n.k === "result" && n.id.startsWith("c:") && n.status !== "missing" && s !== null && s > most) [best, most] = [n.id, s];
  }
  return best;
}

const yours: TourStep = {
  key: "yours",
  cam: "yours",
  sel: () => null,
  title: () => "Now it's yours",
  body: (gi) => {
    const start = ["d:SPY", "p:risk.mc_atlas", "p:kronos.shadow", "kill", "f:VIXCLS", bestCell(gi), "feed:sec"]
      .filter((id): id is string => id !== null && gi.byId.has(id));
    return paras(
      segs("Drag to turn the machine, click anything to read it, or search above."),
      start.length > 0 && S`Some places to start: ${list(start.map((id) => ref(gi, id)))}.`,
      segs("The pages along the top work with each part directly: Markets, Risk, Portfolios, Research, Volatility, Rates and Companies."),
    );
  },
};

/** The tour, in order: inward from the world's data to the book, then the machine handed over. */
export const STEPS: TourStep[] = [overview, prices, nvda, clock, farm, overlay, book, yours];

/** Step `step` (1 to 8) as a view sees it: the node it selects, by id, and where it puts the camera. */
export function stepView(gi: GraphIndex, step: number, ctx: ReadingCtx): StepView {
  const s = STEPS[step - 1];
  const i = s.sel(gi, ctx);
  return { sel: i === null ? null : gi.out.nodes[i].id, cam: s.cam };
}
