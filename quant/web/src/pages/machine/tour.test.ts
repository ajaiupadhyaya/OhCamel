import { describe, expect, it } from "vitest";
import { FIXTURE, FIXTURE_BROKEN, FIXTURE_NO_SUMMARY } from "./fixture";
import type { ReadingCtx, Seg } from "./prose";
import { allowedNumbers, numbersIn } from "./testkit";
import { STEPS, stepView } from "./tour";
import type { TourStep } from "./tour";
import type { MachineOut, MNode, Run } from "./types";
import { indexGraph } from "./web";

const ctxOf = (fx: MachineOut): ReadingCtx => ({ runs: fx.runs, now: new Date(fx.asof) });

describe("stepView: a step as a view sees it", () => {
  const gi = indexGraph(FIXTURE);

  it("names a step's node by id, with its camera", () => {
    expect(stepView(gi, 3, ctxOf(FIXTURE))).toEqual({ sel: "d:NVDA", cam: "node" });
  });

  it("names nothing for a step that selects nothing", () => {
    expect(stepView(gi, 1, ctxOf(FIXTURE))).toEqual({ sel: null, cam: "overview" });
    expect(stepView(gi, 8, ctxOf(FIXTURE))).toEqual({ sel: null, cam: "yours" });
  });
});
const step = (key: string): TourStep => {
  const s = STEPS.find((x) => x.key === key);
  if (!s) throw new Error(`no step ${key}`);
  return s;
};
const line = (p: Seg[]): string => p.map((s) => ("b" in s ? s.b : s.t)).join("");
/** A step's body on `fx` as plain text, a paragraph a line. */
const body = (fx: MachineOut, key: string, ctx = ctxOf(fx)): string => step(key).body(indexGraph(fx), ctx).map(line).join("\n");
/** The node id a step selects on `fx`, or null. */
function selId(fx: MachineOut, key: string, ctx = ctxOf(fx)): string | null {
  const i = step(key).sel(indexGraph(fx), ctx);
  return i === null ? null : fx.nodes[i].id;
}
const refs = (fx: MachineOut, key: string, ctx = ctxOf(fx)): string[] =>
  step(key).body(indexGraph(fx), ctx).flat().flatMap((s) => ("ref" in s ? [s.ref] : []));
/** A writable copy of a fixture. */
const clone = (fx: MachineOut = FIXTURE): MachineOut => structuredClone(fx) as MachineOut;
const nodeOf = (fx: MachineOut, id: string): MNode => {
  const n = fx.nodes.find((x) => x.id === id);
  if (!n) throw new Error(`no node ${id}`);
  return n;
};
/** `fx` with node `id` (and every edge touching it) taken out. */
function without(fx: MachineOut, id: string): MachineOut {
  const out = clone(fx);
  const i = out.nodes.findIndex((n) => n.id === id);
  if (i < 0) throw new Error(`no node ${id}`);
  out.nodes.splice(i, 1);
  out.edges = out.edges.filter(([a, b]) => a !== i && b !== i).map(([a, b, k]) => [a > i ? a - 1 : a, b > i ? b - 1 : b, k]);
  return out;
}
/** `fx` with node `id` marked missing, its fields gone, as the server sends a source it could not read. */
function missing(fx: MachineOut, id: string, why: string): MachineOut {
  const out = clone(fx);
  const n = nodeOf(out, id);
  n.status = "missing";
  n.why = why;
  n.m = n.k === "product" ? { kind: n.id.slice(2) } : {};
  return out;
}
/** `fx` with an edge from `a` to `b`. */
function link(fx: MachineOut, a: string, b: string, kind: MachineOut["edges"][number][2] = "read"): MachineOut {
  const out = clone(fx);
  const at = (id: string): number => out.nodes.findIndex((n) => n.id === id);
  out.edges.push([at(a), at(b), kind]);
  return out;
}
const run = (id: string, kind: string, state: Run["state"], start: string | null, end: string | null, cpu: number | null): Run =>
  ({ id, kind, state, start, end, cpu, progress: state === "done" ? 1 : null, msg: null });

const FIXTURES = [["full", FIXTURE], ["no summary", FIXTURE_NO_SUMMARY], ["broken", FIXTURE_BROKEN]] as const;

describe("the tour: its steps", () => {
  it("has eight steps, walking inward from the world to the book, each with its camera", () => {
    expect(STEPS.map((s) => s.key)).toEqual(["overview", "prices", "nvda", "clock", "farm", "overlay", "book", "yours"]);
    expect(STEPS.map((s) => s.cam)).toEqual(["overview", "node", "node", "node", "node", "node", "node", "yours"]);
  });

  it("selects one real thing per step on FIXTURE, or nothing by design", () => {
    expect(STEPS.map((s) => selId(FIXTURE, s.key))).toEqual([
      null, "feed:prices", "d:NVDA", "j:farm.sweep", "p:farm.sweep", "p:signals.overlay", "book", null,
    ]);
  });

  it("selects nothing for a subject the graph does not have", () => {
    expect(selId(without(FIXTURE, "feed:prices"), "prices")).toBeNull();
    expect(selId(without(FIXTURE, "d:NVDA"), "nvda")).toBeNull();
    expect(selId(without(FIXTURE, "p:farm.sweep"), "farm")).toBeNull();
    expect(selId(without(FIXTURE, "p:signals.overlay"), "overlay")).toBeNull();
    expect(selId(without(FIXTURE, "book"), "book")).toBeNull();
  });

  it("still selects a subject that is present but missing, whose reading says why", () => {
    expect(selId(missing(FIXTURE, "p:farm.sweep", "no farm.sweep artifact has been published"), "farm")).toBe("p:farm.sweep");
  });
});

describe("the tour: honesty", () => {
  for (const [name, fx] of FIXTURES) {
    it(`no step on the ${name} fixture prints a number the response does not carry`, () => {
      const gi = indexGraph(fx), allowed = allowedNumbers(fx), ctx = ctxOf(fx);
      for (const s of STEPS) {
        const text = [s.title(gi, ctx), ...s.body(gi, ctx).map(line)].join("\n");
        for (const n of numbersIn(text)) expect(allowed, `${s.key}: ${n}`).toContain(n);
      }
    });

    it(`every step on the ${name} fixture is plain text in whole sentences, with no gap, and links only nodes that exist`, () => {
      const gi = indexGraph(fx), ctx = ctxOf(fx);
      for (const s of STEPS) {
        const title = s.title(gi, ctx), paras = s.body(gi, ctx);
        expect(title, s.key).not.toBe("");
        expect(paras.length, s.key).toBeGreaterThan(0);
        for (const p of paras) {
          const t = line(p);
          expect(t, s.key).toMatch(/^[A-Z0-9−+].*[.”]$/s);
          expect(t, s.key).not.toMatch(/ {2}|^\s|\s$| [.,;:]|NaN|undefined|null|—|[<>]/);
          expect(t, s.key).not.toMatch(/#[0-9a-f]{3,6}\b|gradient|green/i);
          expect(p.every((seg) => ("b" in seg ? seg.b : seg.t) !== ""), s.key).toBe(true);
          for (const seg of p) if ("ref" in seg) expect(gi.byId.has(seg.ref), `${s.key} links ${seg.ref}`).toBe(true);
        }
      }
    });
  }

  it("the honesty check fails a step that carries the prototype's hard-coded figures", () => {
    const allowed = allowedNumbers(FIXTURE);
    // the prototype's STEPS: 563 tickers, 1,204 series, 421 names, 64 minutes, 3,846 CPU-seconds, a Sharpe of 2.39, 23 strategies
    const prototype = "563 tickers, 1,204 series, 421 names for 64 minutes and 3,846 CPU-seconds, a Sharpe of 2.39, 23 strategies at 11:15 PM";
    expect(numbersIn(prototype).filter((n) => !allowed.has(n))).toEqual(["563", "1,204", "421", "64", "3,846", "2.39", "23", "11:15 PM"]);
  });

  it("allowedNumbers vouches for the heaviest run's time, duration and CPU-seconds, the payload's own time, and no other run's", () => {
    const allowed = allowedNumbers(FIXTURE);
    for (const n of ["11:00 PM", "27 Oct", "91", "5,232", "6:51 PM"]) expect(allowed, n).toContain(n);
    // the 2:02 PM league run and the desk report's 44 s run are not the heaviest; their times and spans are not printed
    for (const n of ["2:02 PM", "44"]) expect(allowed, n).not.toContain(n);
    // with no runs there is no heaviest, and no span
    const fx = clone();
    fx.runs = [];
    expect(allowedNumbers(fx)).not.toContain("91");
  });
});

describe("the tour: 1, the overview", () => {
  it("is the machine at the payload's New York time, on its weekday, in words", () => {
    const gi = indexGraph(FIXTURE);
    expect(step("overview").title(gi, ctxOf(FIXTURE))).toBe("The machine at 6:51 PM on a Wednesday");
    const fx = clone();
    fx.asof = "2026-10-29T02:05:00Z";   // 10:05 PM on Wednesday in New York, Thursday in UTC
    expect(step("overview").title(indexGraph(fx), ctxOf(fx))).toBe("The machine at 10:05 PM on a Wednesday");
  });

  it("is the page's own time, as the top bar's, and not the time of the graph's last build", () => {
    const gi = indexGraph(FIXTURE);
    expect(step("overview").title(gi, { runs: FIXTURE.runs, now: new Date("2026-10-30T19:45:00Z") })).toBe("The machine at 3:45 PM on a Friday");
  });

  it("is just the machine when the page's clock does not read", () => {
    expect(step("overview").title(indexGraph(FIXTURE), { runs: FIXTURE.runs, now: new Date(NaN) })).toBe("The machine");
  });

  it("counts the desk's strategies from the graph", () => {
    const t = body(FIXTURE, "overview");
    expect(t).toContain("the desk's 2 strategies");
    expect(t).toMatch(/rings that narrow to one book/);
    expect(t).not.toMatch(/23/);
  });
});

describe("the tour: 2, the price feed", () => {
  it("gives the daily job's New York time and the warehouse's tracks, counted", () => {
    const t = body(FIXTURE, "prices");
    expect(t).toContain("On weekdays at 6:30 PM, New York time, the Daily bars job pulls this feed's daily bars into the warehouse.");
    expect(t).toContain(
      "The rings just inside the feeds are the warehouse's tracks: daily bars for 3 tickers, one-minute bars for 1 name, option chains for 1 name, 1 FRED series and SEC filings for 1 company.",
    );
    expect(t).toContain("That is 7 series in all.");
    expect(refs(FIXTURE, "prices")).toContain("j:ingest.bars_daily");
  });

  it("counts every track it has, and names none it lacks", () => {
    const fx = clone();
    fx.nodes.push(
      { id: "ff:FF5", k: "series", l: "FF5", n: "Fama-French five factors", ring: "daily", at: { sec: "ff", i: 0 }, m: { track: "factors" } },
      { id: "13f:0001067983", k: "series", l: "BRK", n: "Berkshire Hathaway: 13F", ring: "daily", at: { sec: "13f", i: 0 }, m: { track: "13f" } },
    );
    const t = body(fx, "prices");
    expect(t).toContain("1 factor series");
    expect(t).toContain("the 13F holdings of 1 fund manager");
    expect(t).toContain("9 series in all");
    const fy = without(without(FIXTURE, "o:NVDA"), "m:NVDA");
    expect(body(fy, "prices")).not.toMatch(/one-minute|option/);
  });

  it("says a stale feed's data is older than its schedule allows, with no word of an artifact, instead of what it pulls", () => {
    const fx = clone();
    nodeOf(fx, "feed:prices").m.stale = true;
    const t = body(fx, "prices");
    expect(t).toContain("The price feed's newest data is from 28 Oct, older than its schedule allows.");
    expect(t).not.toMatch(/artifact|pulls/);
  });

  it("says the feed is missing, and why, instead of saying what it pulls", () => {
    const t = body(missing(FIXTURE, "feed:prices", "The warehouse has no record of this feed yet."), "prices");
    expect(t).toContain("The price feed is missing from the machine. The warehouse has no record of this feed yet.");
    expect(t).not.toMatch(/pulls/);
    expect(t).toContain("That is 7 series in all.");   // the warehouse's tracks are other nodes, still there
  });

  it("says the feed is not in the machine when the graph lacks it", () => {
    const t = body(without(FIXTURE, "feed:prices"), "prices");
    expect(t).toContain("The price feed is not in the machine.");
    expect(t).not.toMatch(/pulls/);
  });

  it("says nothing of a pull the graph does not draw", () => {
    const fx = clone();
    const f = fx.nodes.findIndex((n) => n.id === "feed:prices");
    fx.edges = fx.edges.filter(([a]) => a !== f);
    expect(body(fx, "prices")).not.toMatch(/pulls/);
  });
});

describe("the tour: 3, one stock", () => {
  it("names NVDA's tracks, its direct readers' count, and where its minute bars go", () => {
    const t = body(FIXTURE, "nvda");
    expect(t).toContain("The warehouse keeps NVDA's daily bars, one-minute bars, option chain and SEC filings.");
    expect(t).toContain("Its daily bars are read directly by 1 job; its one-minute bars feed the Vol forecast league.");
    expect(refs(FIXTURE, "nvda")).toEqual(expect.arrayContaining(["d:NVDA", "m:NVDA", "o:NVDA", "s:NVDA", "j:vol.forecast_league"]));
  });

  it("says where its option chain goes only when an edge says so, and its minute bars likewise", () => {
    expect(body(FIXTURE, "nvda")).not.toMatch(/option chain feeds/);
    const fx = clone();
    fx.nodes.push({ id: "j:vol.surface_history", k: "job", l: "Vol surface", n: "Vol surface", ring: "clock", at: { h: 17 }, m: { kind: "vol.surface_history" } });
    const t = body(link(fx, "o:NVDA", "j:vol.surface_history"), "nvda");
    expect(t).toContain("its one-minute bars feed the Vol forecast league and its option chain feeds the Vol surface.");
    const fy = clone();
    const m = fy.nodes.findIndex((n) => n.id === "m:NVDA");
    fy.edges = fy.edges.filter(([a]) => a !== m);
    expect(body(fy, "nvda")).not.toMatch(/one-minute bars feed/);
  });

  it("reaches the book through the overlay when no strategy depends on it, and names a limit only when one reads it", () => {
    const t = body(FIXTURE, "nvda");
    expect(t).toContain("No strategy on the desk depends on it, but it still reaches the book: through the exposure overlay, which scales the whole book.");
    expect(refs(FIXTURE, "nvda").some((id) => id.startsWith("lim:"))).toBe(false);
    const fx = clone();
    const [aapl, cap] = ["d:AAPL", "lim:aapl-cap"].map((id) => fx.nodes.findIndex((n) => n.id === id));
    const nvda = fx.nodes.findIndex((n) => n.id === "d:NVDA");
    fx.edges = fx.edges.map((e) => (e[0] === aapl && e[1] === cap ? [nvda, cap, "read"] : e));
    expect(body(fx, "nvda")).toContain("through the exposure overlay, which scales the whole book, and through its own limit, aapl-cap.");
  });

  it("counts the strategies it reaches when it reaches some", () => {
    const t = body(link(FIXTURE, "d:NVDA", "j:farm.sweep"), "nvda");
    expect(t).toContain("It reaches 1 of the desk's 2 strategies and the book.");
    expect(t).not.toMatch(/No strategy/);
  });

  it("says NVDA is missing, and why, and asserts nothing of it", () => {
    const t = body(missing(FIXTURE, "d:NVDA", "The warehouse holds no bars for it."), "nvda");
    expect(t).toContain("NVDA is missing from the machine. The warehouse holds no bars for it.");
    expect(t).not.toMatch(/keeps|read directly|reaches/);
    expect(body(without(FIXTURE, "d:NVDA"), "nvda")).toContain("NVDA is not in the machine.");
  });
});

describe("the tour: 4, the clock", () => {
  it("selects the heaviest run's job, chosen from the runs and not by name", () => {
    expect(selId(FIXTURE, "clock")).toBe("j:farm.sweep");
    const fx = clone();
    fx.runs[0].cpu = 9999;   // the 01:30Z league run is now the heaviest
    expect(selId(fx, "clock")).toBe("j:vol.forecast_league");
    expect(body(fx, "clock")).toContain("The heaviest job of the last day was the Vol forecast league");
  });

  it("states the heaviest job's name, run time, duration and CPU-seconds, and what it reads directly", () => {
    const t = body(FIXTURE, "clock");
    expect(t).toContain("The heaviest job of the last day was the Strategy farm: its run at 11:00 PM on 27 Oct took 91 min and 5,232 CPU-seconds. It reads 1 series directly.");
    expect(t).toContain("The clock is a dial of the day in New York time, with 7 jobs at their hours.");
    expect(refs(FIXTURE, "clock")).toContain("j:farm.sweep");
  });

  it("says which job is running now, with its progress, and points to the day", () => {
    const t = body(FIXTURE, "clock");
    expect(t).toContain("Daily signals is running now, 70% through.");
    expect(t).toMatch(/The day tab lists every run of the last day/);
  });

  it("states no duration for a heaviest run without a start or an end, only what the run has", () => {
    const fx = clone();
    fx.runs[1].end = null;
    expect(body(fx, "clock")).toContain("its run at 11:00 PM on 27 Oct took 5,232 CPU-seconds.");
    const fy = clone();
    fy.runs[1].start = null;
    expect(body(fy, "clock")).toContain("The heaviest job of the last day was the Strategy farm: its run took 5,232 CPU-seconds.");
  });

  it("selects nothing and says no job has run when there are no runs", () => {
    const fx = clone();
    fx.runs = [];
    expect(selId(fx, "clock")).toBeNull();
    const t = body(fx, "clock");
    expect(t).toContain("No job has run in the last day.");
    expect(t).not.toMatch(/heaviest|running now/);
  });

  it("does not say no job ran when runs finished but none cleanly with a CPU figure", () => {
    const now = new Date(FIXTURE.asof);
    const ctx = { runs: [run("a", "farm.sweep", "failed", "2026-10-28T03:00:00Z", "2026-10-28T03:10:00Z", 600)], now };
    expect(step("clock").sel(indexGraph(FIXTURE), ctx)).toBeNull();
    const t = body(FIXTURE, "clock", ctx);
    expect(t).not.toContain("No job has run");
    expect(t).toContain("No run in the last day both finished cleanly and recorded its CPU-seconds, so none is singled out as the heaviest.");
  });

  it("names the heaviest run's job by its kind when the graph has no such job, and says when the job is missing", () => {
    const now = new Date(FIXTURE.asof);
    const ctx = { runs: [run("a", "ingest.sec", "done", "2026-10-28T03:00:00Z", "2026-10-28T03:10:00Z", 600)], now };
    expect(step("clock").sel(indexGraph(FIXTURE), ctx)).toBeNull();
    expect(body(FIXTURE, "clock", ctx)).toContain("The heaviest job of the last day was the ingest.sec job: its run at 11:00 PM on 27 Oct took 10 min and 600 CPU-seconds.");
    const t = body(missing(FIXTURE, "j:farm.sweep", "It is not on the schedule."), "clock");
    expect(t).toContain("The job is missing from the machine. It is not on the schedule.");
    expect(t).not.toMatch(/reads 1 series/);
  });
});

describe("the tour: 5, the farm", () => {
  it("gives the cells, trials, best Sharpe and how many pass, from its leaderboard", () => {
    const t = body(FIXTURE, "farm");
    expect(t).toContain("On weekdays at 11:00 PM the strategy farm re-runs its backtests.");
    expect(t).toContain("As of 12:31 AM on 28 Oct, its latest run has 2 cells with a result, and the farm has run 48 trials in all.");
    expect(t).toContain("The best out-of-sample Sharpe is 0.81, from tsmom on us equity indices. After deflating for every trial, 1 of the 2 cells passes every gate.");
    expect(refs(FIXTURE, "farm")).toEqual(expect.arrayContaining(["p:farm.sweep", "c:tsmom@us_equity_indices"]));
  });

  it("says no cell passes when none does, and that the failures are shown", () => {
    const fx = clone();
    const lens = nodeOf(fx, "p:farm.sweep").m.lens as { board: unknown[][] };
    lens.board[0][1] = "FAIL";
    expect(body(fx, "farm")).toContain("but after deflating for every trial, no cell passes. The failures are shown, not hidden.");
  });

  it("says plainly that the leaderboard could not be read, and gives none of its figures", () => {
    const t = body(FIXTURE_BROKEN, "farm");
    expect(t).toContain("Its leaderboard is not given here. This figure could not be read from the latest run.");
    expect(t).not.toMatch(/0\.81|passes|48/);
  });

  it("says the farm is missing, and why, and gives none of its figures", () => {
    const t = body(missing(FIXTURE, "p:farm.sweep", "Its job has not published anything yet."), "farm");
    expect(t).toBe("The strategy farm is missing from the machine. Its job has not published anything yet.");
    expect(body(without(FIXTURE, "p:farm.sweep"), "farm")).toBe("The strategy farm is not in the machine.");
  });

  it("says a scheduled run of the farm was missed, and gives none of its figures", () => {
    const fx = clone();
    nodeOf(fx, "p:farm.sweep").m.stale = true;
    const t = body(fx, "farm");
    expect(t).toContain("The strategy farm's latest run finished at 12:31 AM on 28 Oct, and a scheduled one has been missed since, so its figures are not given here.");
    expect(t).not.toMatch(/0\.81|passes|48 trials/);
  });
});

describe("the tour: 6, the overlay", () => {
  it("gives the forecast, target and scale parsed from the overlay's note, as of its latest run", () => {
    const t = body(FIXTURE, "overlay");
    expect(t).toContain("As of 6:40 PM on 28 Oct, it forecasts the combined book's volatility at 3.4% against a 10% target, so the scale is 1.00, the most it can be.");
    expect(t).toContain("Daily signals writes each farm strategy's signal document for the desk to size.");
    expect(t).toContain("The exposure overlay reads the Vol forecast league and sets one exposure scale for the whole desk, toward a volatility target.");
    expect(refs(FIXTURE, "overlay")).toEqual(expect.arrayContaining(["p:signals.daily", "p:signals.overlay", "p:vol.forecast_league"]));
  });

  it("points to daily signals' own notes only when they say how the farm's tests differ", () => {
    expect(body(FIXTURE, "overlay")).toContain("Select Daily signals to read, in its own notes, how the farm's tests differ from what the desk trades.");
    const fx = clone();
    nodeOf(fx, "p:signals.daily").m.notes = ["desk unreachable: keyed on the last written document"];   // a caveat, but not that one
    expect(body(fx, "overlay")).not.toMatch(/own notes/);
  });

  it("gives no scale when no note states one", () => {
    const fx = clone();
    nodeOf(fx, "p:signals.overlay").m.notes = ["no vol.forecast_league artifact: EWMA(0.94) on every name's diagonal"];
    const t = body(fx, "overlay");
    expect(t).toContain("No note from its latest run states the scale, so none is given here.");
    expect(t).not.toMatch(/3\.4%|1\.00|10%/);
  });

  it("says the overlay is missing, and why, and gives none of its figures", () => {
    const t = body(missing(FIXTURE, "p:signals.overlay", "Its job has not published anything yet."), "overlay");
    expect(t).toContain("The exposure overlay is missing from the machine. Its job has not published anything yet.");
    expect(t).not.toMatch(/3\.4%|1\.00|10%|exposure scale/);
    expect(body(without(FIXTURE, "p:signals.overlay"), "overlay")).toContain("The exposure overlay is not in the machine.");
  });

  it("says a scheduled run of the overlay was missed, and gives none of its figures", () => {
    const fx = clone();
    nodeOf(fx, "p:signals.overlay").m.stale = true;
    const t = body(fx, "overlay");
    expect(t).toContain("The exposure overlay's latest run finished at 6:40 PM on 28 Oct, and a scheduled one has been missed since, so its figures are not given here.");
    expect(t).not.toMatch(/3\.4%|1\.00/);
  });
});

describe("the tour: 7, the book", () => {
  it("gives the strategies, their capital, the cap, and the live book as of the summary's time", () => {
    const t = body(FIXTURE, "book");
    expect(t).toContain("Everything ends here: 2 strategies at 2.5% of capital each, under a 60% incubation cap.");
    expect(t).toContain("As of 4:30 PM on 28 Oct, the desk report's book summary shows 14 positions, 2 limits and the kill switch armed. If dd-cap breaks, the kill switch halts the book.");
  });

  it("gives the range of capital when the strategies' shares differ", () => {
    const fx = clone();
    nodeOf(fx, "st:exp_a01_spy").m.capital = 0.01;
    expect(body(fx, "book")).toContain("2 strategies at 1% to 2.5% of capital, under a 60% incubation cap.");
  });

  it("gives the plain reason when there is no summary, and no limit, kill switch or position", () => {
    const t = body(FIXTURE_NO_SUMMARY, "book");
    expect(t).toContain("Its limits, kill switch and position count are not shown here. The desk report ran but could not read the live book.");
    expect(t).not.toMatch(/system's reason|“/);
    expect(t).not.toMatch(/armed|positions|2 limits|dd-cap/);
  });

  it("never gives a threshold, a position's name or a size", () => {
    const fx = clone();
    Object.assign(nodeOf(fx, "lim:dd-cap").m, { threshold: 0.15 });
    const t = body(fx, "book");
    expect(t).not.toMatch(/15%|0\.15|threshold|NVDA|SPY/);
  });

  it("says the book is missing, and why, and gives none of its figures", () => {
    const t = body(missing(FIXTURE, "book", "The roster was not read."), "book");
    expect(t).toBe("The book is missing from the machine. The roster was not read.");
    expect(body(without(FIXTURE, "book"), "book")).toBe("The book is not in the machine.");
  });
});

describe("the tour: 8, now it's yours", () => {
  it("links only the starting points that exist, the best farm cell among them", () => {
    expect(refs(FIXTURE, "yours")).toEqual(["d:SPY", "kill", "f:VIXCLS", "c:tsmom@us_equity_indices"]);
    expect(refs(FIXTURE_NO_SUMMARY, "yours")).toEqual(["d:SPY", "f:VIXCLS", "c:tsmom@us_equity_indices"]);
    expect(body(FIXTURE, "yours")).toContain("Some places to start: SPY, the kill switch, VIXCLS and tsmom on us equity indices.");
  });

  it("picks the farm cell with the best Sharpe from the data, and every listed id when present", () => {
    const fx = clone();
    nodeOf(fx, "c:xsmom@sectors").m.oos_sharpe_ann = 1.4;
    fx.nodes.push(
      { id: "p:risk.mc_atlas", k: "product", l: "P5", n: "Risk atlas", ring: "products", at: {}, m: { kind: "risk.mc_atlas" } },
      { id: "p:kronos.shadow", k: "product", l: "KS", n: "Kronos shadow", ring: "products", at: {}, m: { kind: "kronos.shadow" } },
      { id: "feed:sec", k: "feed", l: "Filings", n: "Filings from SEC EDGAR", ring: "feeds", at: {}, m: {} },
    );
    expect(refs(fx, "yours")).toEqual(["d:SPY", "p:risk.mc_atlas", "p:kronos.shadow", "kill", "f:VIXCLS", "c:xsmom@sectors", "feed:sec"]);
  });
});

describe("the tour: robustness", () => {
  it("never throws or prints a gap when every node's fields are missing and there are no runs", () => {
    const fx = clone();
    fx.nodes.forEach((n) => (n.m = {}));
    const gi = indexGraph(fx), ctx = { runs: [], now: new Date(fx.asof) };
    for (const s of STEPS) {
      const t = [s.title(gi, ctx), ...s.body(gi, ctx).map(line)].join("\n");
      expect(t, s.key).not.toMatch(/NaN|undefined|null|—/);
      expect(s.body(gi, ctx).length, s.key).toBeGreaterThan(0);
    }
  });

  it("says what is absent on an empty graph, and selects nothing", () => {
    const fx: MachineOut = { ...clone(), nodes: [], edges: [], runs: [] };
    const gi = indexGraph(fx), ctx = { runs: [], now: new Date(fx.asof) };
    for (const s of STEPS) {
      expect(s.sel(gi, ctx), s.key).toBeNull();
      const t = s.body(gi, ctx).map(line).join("\n");
      expect(t, s.key).not.toMatch(/NaN|undefined|null|—/);
      expect(s.body(gi, ctx).length, s.key).toBeGreaterThan(0);
    }
  });
});
