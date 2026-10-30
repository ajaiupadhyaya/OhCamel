/**
 * Product readings (spec §6.5): what each published artifact says, from its manifest fields, its lens and its
 * own notes. Every product opens with its verdict and when its latest artifact finished, then says what is
 * specific to its kind, then who reads it. The notes it writes about itself are quoted whole as caveats,
 * except a note the reading has just stated in words.
 */
import { num, nyDay, pct, plural } from "./fmt";
import { parseDailyNote, parseOverlayFallback, parseOverlayNote } from "./notes";
import {
  at, B, capital, cellRef, estimator, fin, known, list, node, obj, ofDesk, paras, readers, reaches, reason, ref, refAt, rows, S, segs, share, shares, str, strs, ups, downs,
} from "./prose";
import type { Reading, Seg } from "./prose";
import type { MNode } from "./types";
import { directReaders } from "./web";
import type { GraphIndex } from "./web";

/** The kind-specific paragraphs and caveats of a product with a manifest. */
type Body = { paras: Seg[][]; caveats: string[] };
type ProductReading = (gi: GraphIndex, i: number, m: Record<string, unknown>, notes: string[]) => Body;

/** A lens that could not be read, said plainly, with the reason the server gives. */
const lensWhy = (gi: GraphIndex, m: Record<string, unknown>, what: string): Seg[] =>
  str(m.lens_why) ? S`${what} could not be read, so its figures are not given here: ${reason(gi, str(m.lens_why)!)}.` : [];

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
      best && str(best[0]) && S`The best out-of-sample Sharpe is ${B(num(best[2] as number, 2))}, from ${cellRef(gi, best[0] as string)}${
        fin(best[3]) !== null && S`, with a DSR of ${B(num(best[3] as number, 2))}`}. `,
      board.length > 0 && (pass === 0 ? "No cell passes." : S`${B(num(pass))} of the ${num(board.length)} cells ${pass === 1 ? "passes" : "pass"} every gate.`),
    );
  } else {
    first = segs(total > 0 && S`Its latest run has ${B(plural(total, "cell"))}. `, lensWhy(gi, m, "Its leaderboard"));
  }
  const st = gi.out.nodes.map((_, j) => j).filter((j) => node(gi, j).k === "strategy" && ups(gi, j, "result").some((c) => node(gi, c).id.startsWith("c:")));
  const caps = st.map((j) => fin(node(gi, j).m.capital)).filter((c): c is number => c !== null);
  const desk = st.length > 0 &&
    S`${ofDesk(gi, st.length)} ${st.length === 1 ? "is a farm cell" : "are farm cells"}${caps.length > 0 && [", at ", shares(caps), " of capital", st.length > 1 && " each"]}.`;
  return { paras: paras(first, desk), caveats: notes };
};

// daily.py:83-89: the two execution notes, found by what they say (a stray "open" elsewhere is not one)
const LEVERAGE = (s: string): boolean => /\bleverage\b/i.test(s) && /\bverdict\b/i.test(s);
const FILLS = (s: string): boolean => /\bfills?\b/i.test(s) && /\bclose\b/i.test(s) && /\bopen\b/i.test(s);

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
    paras: paras(
      segs("This is where research becomes orders: it writes each farm strategy's signal document for the desk to size.", counts && [" ", counts]),
      caveats.length > 0 && S`It notes ${B(num(caveats.length))} ${caveats.length === 1 ? "way" : "ways"} the farm's tests differ from what the desk trades.`,
    ),
    caveats,
  };
};

const overlay: ProductReading = (gi, _i, _m, notes) => {
  const lead = "One number for the whole desk: an exposure scale toward a volatility target.";
  const o = parseOverlayNote(notes);
  if (!o) return { paras: paras(segs(lead, notes.length > 0 && " No note gives its scale, so its notes are quoted as written.")), caveats: notes };
  // the notes this reading states in words; every other note is quoted (stale inputs, a missing league, a bad variance)
  const exposure = notes.find((s) => parseOverlayNote([s]) !== null);
  const fellBack = o.estimator !== null && parseOverlayFallback(notes) === o.estimator;
  const est = o.estimator && o.sessions !== 0 && estimator(gi, o.estimator);
  const fallback = est && fellBack ? notes.find((s) => parseOverlayFallback([s]) !== null) : undefined;
  const sessions = o.sessions !== null && o.sessions > 0 && S` over ${B(num(o.sessions))} sessions`;
  return {
    paras: paras(segs(
      lead,
      S` On its latest run the combined book forecasts ${o.forecast > 0 ? [B(pct(o.forecast, 1)), " volatility"] : "no volatility"} against a ${
        B(share(o.target))} target, so the scale is ${B(num(o.scale, 2))}${o.scale >= 1 && ", the most it can be"}.`,
      est && (fellBack
        ? S` The covariance league gave no winner it could use, so it fell back to ${est}${sessions}.`
        : S` It estimates covariance with ${est}${sessions}.`),
      exposure !== undefined && /reported, never used/.test(exposure) &&
        " The regime model's probability and the Monte Carlo VaR are reported beside it, and never used.",
    )),
    caveats: notes.filter((s) => s !== exposure && s !== fallback),
  };
};

const volLeague: ProductReading = (gi, _i, m, notes) => {
  const models = rows(obj(m.lens).models).filter((r) => str(r[0]) && fin(r[1]) !== null).sort((a, b) => (b[1] as number) - (a[1] as number));
  const same = new Set(models.map((r) => fin(r[3]))).size === 1 && fin(models[0]?.[3]) !== null;
  const item = (r: unknown[], k: number): Seg[] => S`${ref(gi, `lm:${r[0]}`, String(r[0]).toUpperCase())}${k === 0 && " stays in the statistical best set"} for ${
    B(pct(r[1] as number, 1))}${same && k === 0 && " of them"}${!same && fin(r[3]) !== null && S` of its ${B(num(r[3] as number))} names`}`;
  const body = models.length > 0 && S`${same && S`Scored across ${B(num(models[0][3] as number))} names, `}${list(models.map(item))}.`;
  return { paras: paras(body || lensWhy(gi, m, "Its model table")), caveats: notes };
};

const covLeague: ProductReading = (gi, _i, m, notes) => {
  const wins = rows(obj(m.lens).estimators).filter((r) => r[3] === 1 && str(r[0]) && str(r[1]) && fin(r[2]) !== null);
  const body = wins.length > 0 && S`The winner on each universe: ${list(wins.map((r, k) =>
    S`${estimator(gi, String(r[0]))} on ${String(r[1]).replace(/_/g, " ")} (${B(pct(r[2] as number, 1))}${k === 0 && " realized volatility"})`))}.`;
  return { paras: paras(body || lensWhy(gi, m, "Its league table")), caveats: notes };
};

const METHOD: Record<string, string> = { fhs: "FHS", copula_t: "t-copula" };
const atlas: ProductReading = (gi, _i, m, notes) => {
  const all = rows(obj(m.lens).measures).filter((r) => str(r[0]) && fin(r[1]) !== null && fin(r[2]) !== null && fin(r[3]) !== null);
  const h = all.some((r) => r[1] === 10) ? 10 : Math.max(...all.map((r) => r[1] as number));
  // a measure named by its node, whose name carries the confidence level; else its method and horizon
  const body = all.filter((r) => r[1] === h).map((r) => S`${gi.byId.has(`am:${r[0]}:${h}`) ? ref(gi, `am:${r[0]}:${h}`) : `${METHOD[r[0] as string] ?? r[0]} ${num(h)}-day VaR and ES`} are ${
    B(pct(r[2] as number, 2))} and ${B(pct(r[3] as number, 2))}${fin(r[4]) !== null && S`, from ${B(num(r[4] as number))} paths`}.`);
  return { paras: paras(body.length > 0 ? segs(...body.map((b, k) => [k ? " " : "", b])) : lensWhy(gi, m, "Its measures table")), caveats: notes };
};

const xs: ProductReading = (gi, _i, m, notes) => {
  const d = rows(obj(m.lens).deciles).filter((r) => fin(r[0]) !== null && fin(r[1]) !== null).sort((a, b) => (a[0] as number) - (b[0] as number));
  const r = d.map((x) => x[1] as number);
  const body = d.length > 1 && S`On its holdout, the top decile by score had a mean next-month return of ${B(pct(r[r.length - 1], 2))} and the bottom decile ${
    B(pct(r[0], 2))}. Returns ${r.every((x, k) => k === 0 || x >= r[k - 1]) ? "climb" : "do not climb"} decile by decile.`;
  return { paras: paras(body || lensWhy(gi, m, "Its decile table")), caveats: notes };
};

const regime: ProductReading = (gi, _i, m, notes) => {
  const lens = obj(m.lens), p = fin(lens.p_high), day = nyDay(str(lens.date)), weeks = fin(lens.weeks);
  const body = p !== null && S`${known(day) && S`As of ${B(day)}, `}it puts the probability of the high-volatility state at ${B(num(p, 2))}${
    weeks !== null && S`, from ${B(num(weeks))} weeks of data`}.`;
  return { paras: paras(body ? capital(body) : lensWhy(gi, m, "Its probabilities")), caveats: notes };
};

const surface: ProductReading = (gi, _i, m, notes) => {
  const lens = obj(m.lens), name = str(lens.underlying), atm = fin(lens.atm30), slices = fin(lens.slices), names = fin(lens.names), day = nyDay(str(lens.asof));
  const body = segs(
    names !== null && S`It keeps a volatility-surface history for ${B(num(names))} underlyings. `,
    name && atm !== null && S`${known(day) && S`On ${B(day)}, `}${ref(gi, `d:${name}`, name)}'s one-month at-the-money implied volatility was ${B(pct(atm, 1))}${
      slices !== null && S`, from ${B(plural(slices, "expiry slice"))}`}.`,
  );
  return { paras: paras(body.length > 0 ? capital(body) : lensWhy(gi, m, "Its history")), caveats: notes };
};

const BY_KIND: Record<string, ProductReading> = {
  "farm.sweep": farm, "signals.daily": daily, "signals.overlay": overlay, "vol.forecast_league": volLeague, "cov.league": covLeague,
  "risk.mc_atlas": atlas, "models.xs_lgbm": xs, "regime.hmm": regime, "vol.surface_history": surface,
};

/** The reading of a product node that is present (a missing one is the dispatcher's). */
export function product(gi: GraphIndex, i: number, n: MNode): Reading {
  const m = n.m, title = n.n || n.l;
  if (n.id === "p:research") return research(gi, i, n);
  const kind = str(m.kind) ?? n.id.replace(/^p:/, "");
  const stale = m.stale === true && "That artifact is past its schedule's max age.";
  if (kind === "desk.report") {
    // spec §7 Q1: its node shows only its run times; of its tables, only the book summary is shown, on the book
    const done = at(m.finished);
    const book = gi.byId.get("book");
    const shown = book !== undefined && str(node(gi, book).m.summary_asof) && S`Of what it reports, this page shows only the book summary, on ${ref(gi, "book")}.`;
    return { title, paras: paras(segs(done.length > 0 && S`Its latest report finished at ${done}. `, stale && [stale, " "], shown)), caveats: [] };
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
  return { title, paras: paras(head, ...body.paras, tail), caveats: body.caveats };
}

function research(gi: GraphIndex, i: number, n: MNode): Reading {
  const st = downs(gi, i, "strategy");
  const k = fin(n.m.strategies) ?? st.length;
  return {
    title: n.n || n.l,
    paras: paras(S`The research service sends signals for ${ofDesk(gi, k)}${st.length > 0 && st.length <= 8 && [": ", list(st.map((j) => refAt(gi, j)))]}.`),
    caveats: [],
  };
}
