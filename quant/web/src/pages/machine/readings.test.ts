import { describe, expect, it } from "vitest";
import { FIXTURE, FIXTURE_BROKEN, FIXTURE_NO_SUMMARY } from "./fixture";
import { executionNotes } from "./products";
import { reading } from "./readings";
import type { Reading } from "./readings";
import { allowedNumbers, flatten, numbersIn, refsOf } from "./testkit";
import type { MachineOut, MNode } from "./types";
import { directReaders, indexGraph } from "./web";

const ctxOf = (fx: MachineOut) => ({ runs: fx.runs, now: new Date(fx.asof) });

/** The reading of node `id` on `fx`. */
function read(fx: MachineOut, id: string, ctx = ctxOf(fx)): Reading {
  const gi = indexGraph(fx);
  const i = gi.byId.get(id);
  if (i === undefined) throw new Error(`no node ${id}`);
  return reading(gi, i, ctx);
}
const text = (fx: MachineOut, id: string): string => flatten(read(fx, id));
const nodeOf = (fx: MachineOut, id: string): MNode => fx.nodes.find((n) => n.id === id)!;
/** A writable copy of FIXTURE. */
const clone = (): MachineOut => structuredClone(FIXTURE) as MachineOut;

describe("testkit", () => {
  it("numbersIn finds every number, including those inside words, percents, times and grouped thousands", () => {
    expect(numbersIn("It returned +41.3% at 6:30 PM on 28 Oct, for 1,241 CPU-seconds, −1.2% and 0.78 (exp a01).")).toEqual([
      "+41.3%", "6:30 PM", "28 Oct", "1,241", "−1.2%", "0.78", "01",
    ]);
    expect(numbersIn("17 held, 3 refused.")).toEqual(["17", "3"]);
    expect(numbersIn("no figures here")).toEqual([]);
  });

  it("allowedNumbers holds the fixture's figures as the readings print them, and not a number the fixture lacks", () => {
    const allowed = allowedNumbers(FIXTURE);
    // a printed field through its formatter, a parsed note, a timestamp in New York time, a run total, counts
    for (const n of ["+41.3%", "43.9%", "1.00", "3.4%", "10%", "504", "6:30 PM", "9:48 PM", "27 Oct", "1,241", "0.81", "586", "0", "3", "29"]) {
      expect(allowed, n).toContain(n);
    }
    // the prototype's hard-coded figures, which this fixture does not carry; and a date never vouches for its day alone
    for (const n of ["23", "21", "30", "34", "99%", "96%", "1.50", "563", "2.39", "421", "3,846", "1,000,000", "14.8%", "28"]) {
      expect(allowed, n).not.toContain(n);
    }
    // a figure the readings do not print is not vouched for: a sparkline point, the last close, a done run's progress
    for (const n of ["1.19", "688.63", "172.35", "100%"]) expect(allowed, n).not.toContain(n);
    expect(allowed.size).toBeLessThan(200);
  });

  it("the honesty check fails a reading that carries a figure the fixture lacks", () => {
    const allowed = allowedNumbers(FIXTURE);
    const fake: Reading = { title: "The book", paras: [[{ t: "One of the desk's " }, { b: "23" }, { t: " strategies, each at 99% of capital, as of 6:30 PM on 28 Oct." }]], caveats: [] };
    expect(numbersIn(flatten(fake)).filter((n) => !allowed.has(n))).toEqual(["23", "99%"]);
  });

  it("allowedNumbers takes numbers from a limit's kind, which a reading prints, and not from a job's, which it does not", () => {
    const fx = clone();
    nodeOf(fx, "j:regime.hmm").m.kind = "ingest.holdings_13f";
    nodeOf(fx, "lim:aapl-cap").m.kind = "var_99";
    const allowed = allowedNumbers(fx);
    expect(allowed).not.toContain("13");
    expect(allowed).toContain("99");
    expect(text(fx, "lim:aapl-cap")).toContain("It is a var 99 limit");
  });

  it("allowedNumbers counts runs over the same last day as the readings", () => {
    const later = new Date("2026-10-29T03:30:00Z");   // the 01:30Z league run is now over a day old
    expect(allowedNumbers(FIXTURE)).toContain("1,241");
    const allowed = allowedNumbers(FIXTURE, later);
    expect(allowed).not.toContain("1,241");
    expect(allowed).toContain("197");
  });
});

describe("reading: honesty", () => {
  for (const [name, fx] of [["full", FIXTURE], ["no summary", FIXTURE_NO_SUMMARY], ["broken", FIXTURE_BROKEN]] as const) {
    it(`every reading is honest on the ${name} fixture`, () => {
      const gi = indexGraph(fx), allowed = allowedNumbers(fx);
      fx.nodes.forEach((_, i) => {
        const text = flatten(reading(gi, i, { runs: fx.runs, now: new Date(fx.asof) }));
        expect(text).not.toMatch(/NaN|undefined|null/);
        for (const n of numbersIn(text)) expect(allowed, `${fx.nodes[i].id}: ${n}`).toContain(n);
      });
    });

    it(`every reading on the ${name} fixture has a title and words, no dash where a figure should be, and caveats quoted whole from its own notes`, () => {
      const gi = indexGraph(fx);
      fx.nodes.forEach((node, i) => {
        const r = reading(gi, i, ctxOf(fx));
        expect(r.title, node.id).not.toBe("");
        expect(r.paras.length, node.id).toBeGreaterThan(0);
        expect(flatten(r), node.id).not.toContain("—");
        for (const p of r.paras) {
          const s = p.map((seg) => ("b" in seg ? seg.b : seg.t)).join("");
          expect(s, node.id).toMatch(/^[A-Z0-9−+].*[.”]$/s);   // whole sentences
          expect(s, node.id).not.toMatch(/ {2}|^\s|\s$| [.,]/);
          expect(p.every((seg) => ("b" in seg ? seg.b : seg.t) !== ""), node.id).toBe(true);
        }
        const notes = Array.isArray(node.m.notes) ? (node.m.notes as string[]) : [];
        for (const c of r.caveats) expect(notes, `${node.id}: ${c}`).toContain(c);
        for (const id of refsOf(r)) expect(gi.byId.has(id), `${node.id} links ${id}`).toBe(true);
      });
    });
  }
});

describe("reading: the exposure overlay", () => {
  it("the overlay reading states forecast, target and scale", () => {
    const t = text(FIXTURE, "p:signals.overlay");
    expect(t).toContain("3.4%");
    expect(t).toContain("10%");
    expect(t).toContain("1.00");
    expect(t).toContain("As of 6:40 PM on 28 Oct, the combined book forecasts 3.4% volatility against a 10% target, so the scale is 1.00");
    expect(t).toContain("It estimates covariance with lw constant corr over 504 sessions");
    expect(t).not.toContain("lw_constant_corr");
    expect(t).toMatch(/reported .*never used/);
  });

  it("quotes every overlay note but the ones it states: a stale input and a missing league are caveats", () => {
    const fx = clone();
    const node = nodeOf(fx, "p:signals.overlay");
    const stale = "cov.league artifact 01M5ZZ is stale (used, as the newest there is)";
    const noLeague = "no vol.forecast_league artifact: EWMA(0.94) on every name's diagonal";
    const fallback = "cov.league 01M5ZZ has no rank-1 row for site_etfs: lw_constant_corr";
    node.m.notes = [stale, noLeague, fallback, ...(node.m.notes as string[])];
    const r = read(fx, "p:signals.overlay");
    expect(r.caveats).toEqual([stale, noLeague]);   // the fallback and the exposure note are said in words
    expect(flatten(r)).toMatch(/fell back to lw constant corr/);
    expect(read(FIXTURE, "p:signals.overlay").caveats).toEqual([]);
  });

  it("says nothing of covariance or sessions when the book had no targets to forecast", () => {
    const fx = clone();
    nodeOf(fx, "p:signals.overlay").m.notes = [
      "no targets yet: no roster document in the signals directory, exposure_scale 1",
      "exposure_scale 1.000000 = min(1, 0.1 / 0.000000); covariance lw_constant_corr on 0 sessions, diagonal from P2's best model by QLIKE",
    ];
    const r = read(fx, "p:signals.overlay");
    expect(flatten(r)).toContain("forecasts no volatility against a 10% target, so the scale is 1.00");
    expect(flatten(r)).not.toMatch(/covariance|sessions|0\.0%/);
    expect(r.caveats).toEqual(["no targets yet: no roster document in the signals directory, exposure_scale 1"]);
  });

  it("quotes the fallback note when, with no targets, the reading does not say it fell back", () => {
    const fx = clone();
    const fallback = "no cov.league artifact: lw_constant_corr (Ledoit-Wolf)";
    nodeOf(fx, "p:signals.overlay").m.notes = [
      fallback,
      "no targets yet: no roster document in the signals directory, exposure_scale 1",
      "exposure_scale 1.000000 = min(1, 0.1 / 0.000000); covariance lw_constant_corr on 0 sessions, diagonal from P2's best model by QLIKE",
    ];
    const r = read(fx, "p:signals.overlay");
    expect(flatten(r)).not.toMatch(/fell back|covariance/);
    expect(r.caveats).toEqual([fallback, "no targets yet: no roster document in the signals directory, exposure_scale 1"]);
  });

  it("says the overlay fell back to its estimator only when a note says so", () => {
    expect(text(FIXTURE, "p:signals.overlay")).not.toMatch(/fell back/);
    const fx = clone();
    const notes = nodeOf(fx, "p:signals.overlay").m.notes as string[];
    notes.unshift("cov.league 01M5 has no rank-1 row for site_etfs: lw_constant_corr");
    expect(text(fx, "p:signals.overlay")).toMatch(/fell back to lw constant corr/);
  });

  it("quotes every note whole when no note gives the scale, and states no figure of its own", () => {
    const fx = clone();
    const notes = [
      "cov.league artifact 01M5ZZ is stale (used, as the newest there is)",
      "no targets yet: no roster document in the signals directory, exposure_scale 1",
      "exposure scale written in a form this page does not read",
    ];
    nodeOf(fx, "p:signals.overlay").m.notes = notes;
    const r = read(fx, "p:signals.overlay");
    expect(r.caveats).toEqual(notes);
    expect(flatten(r)).toContain("No note gives its scale, so its notes are quoted as written.");
    expect(flatten(r)).not.toMatch(/forecast|%/);
  });
});

describe("reading: a daily ticker", () => {
  const nvda = (): Reading => read(FIXTURE, "d:NVDA");

  it("NVDA names only its direct readers", () => {
    const gi = indexGraph(FIXTURE);
    const direct = directReaders(gi, gi.byId.get("d:NVDA")!).map((j) => FIXTURE.nodes[j].id);
    const jobs = refsOf(nvda()).filter((id) => id.startsWith("j:"));
    expect(direct).toEqual(["j:vol.forecast_league"]);
    expect(jobs).toEqual(direct);
    expect(flatten(nvda())).toContain("1 job reads it directly");
  });

  it("gives a year's return and volatility only on a full year of closes, and its tracks", () => {
    const t = flatten(nvda());
    expect(t).toContain("Over the last year it returned +41.3%");
    expect(t).toContain("43.9%");
    expect(t).toMatch(/SEC filings, one-minute bars and an option chain/);
    expect(t).toContain("weekdays at 6:30 PM");
  });

  it("says a short history's return is over the sessions it has, never a year, and gives no volatility it lacks", () => {
    const t = text(FIXTURE, "d:AAPL");
    expect(t).toContain("Over the last 2 sessions it returned −1.2%");
    expect(t).not.toMatch(/year|volatility/);
  });

  it("names the three names it moves with most, with the correlation", () => {
    const fx = clone();
    nodeOf(fx, "d:NVDA").m.peers = [["SPY", 0.78], ["AAPL", 0.61], ["QQQ", 0.59], ["DIA", 0.5]];
    const r = read(fx, "d:NVDA");
    expect(flatten(r)).toContain("It moved most with SPY (0.78), AAPL (0.61) and QQQ (0.59)");
    expect(flatten(r)).not.toContain("DIA");
    expect(refsOf(r)).toEqual(expect.arrayContaining(["d:SPY", "d:AAPL"]));
  });

  it("reaches the book through the overlay when no strategy depends on it, and names a limit only when one reads it", () => {
    const r = nvda();
    expect(flatten(r)).toMatch(/No strategy on the desk depends on it, but it still reaches the book: through the exposure overlay/);
    expect(refsOf(r)).toContain("p:signals.overlay");
    expect(refsOf(r).some((id) => id.startsWith("lim:"))).toBe(false);

    const aapl = read(FIXTURE, "d:AAPL");
    expect(flatten(aapl)).toContain("No job reads it directly");
    expect(flatten(aapl)).toMatch(/still reaches the book: through its own limit, aapl-cap/);
    expect(refsOf(aapl)).toContain("lim:aapl-cap");
    expect(refsOf(aapl)).not.toContain("p:signals.overlay");
  });

  it("counts the strategies it reaches when it reaches some", () => {
    expect(text(FIXTURE, "d:SPY")).toContain("Through them it reaches 1 of the desk's 2 strategies and the book");
  });

  it("leaves out any figure the readout lacks, without a dash (ruling 10)", () => {
    const fx = clone();
    nodeOf(fx, "d:NVDA").m = { track: "daily", last: 172.35 };
    const t = text(fx, "d:NVDA");
    expect(t).not.toMatch(/returned|volatility|moved most|—/);
    const fy = clone();
    nodeOf(fy, "d:NVDA").m = { track: "daily", ret1y: 0.4127, vol: 0.4393 };   // no n: the span is unknown
    expect(text(fy, "d:NVDA")).not.toMatch(/returned|volatility/);
  });
});

describe("reading: other series", () => {
  it("says which track a series is on and what reads it", () => {
    expect(text(FIXTURE, "m:NVDA")).toMatch(/one-minute bars.*1 job reads it directly/s);
    expect(text(FIXTURE, "f:VIXCLS")).toContain("1 job reads it directly");
  });
});

describe("reading: a feed", () => {
  it("gives keys, data as-of, the last pull in New York time and keys behind, and that slow series lag by nature", () => {
    const t = text(FIXTURE, "feed:prices");
    expect(t).toContain("586");
    expect(t).toContain("The newest data is from 28 Oct");
    expect(t).toContain("6:33 PM on 28 Oct");
    expect(t).toMatch(/2 of them end before that date/);
    expect(t).toMatch(/a slow series lags by nature/);
  });

  it("says nothing of lag when no key is behind", () => {
    const fx = clone();
    nodeOf(fx, "feed:prices").m.behind = 0;
    expect(text(fx, "feed:prices")).not.toMatch(/lag|behind|end before/);
  });
});

describe("reading: a job", () => {
  it("states its schedule in New York time, that a heavy job never starts in session, and its runs and cpu in the last day", () => {
    const t = text(FIXTURE, "j:vol.forecast_league");
    expect(t).toContain("weekdays at 9:30 PM");
    expect(t).toMatch(/never starts during the market session/);
    expect(t).toContain("In the last day it ran 2 times, 1 of which failed, for 1,241 CPU-seconds");
    expect(text(FIXTURE, "j:signals.daily")).not.toMatch(/heavy|market session/);
  });

  it("says a running job is running, with its progress, and a queued one is queued, from state alone (ruling 17)", () => {
    expect(text(FIXTURE, "j:signals.daily")).toMatch(/running now, 70% through/);
    const overlay = text(FIXTURE, "j:signals.overlay");
    expect(overlay).toMatch(/a run is queued/i);
    expect(overlay).toContain("29 times a day, from 9:20 AM to 8:10 PM");
    // a run that has just started may not have its start yet: its state still says it is running
    const fx = clone();
    fx.runs.find((r) => r.kind === "signals.daily")!.start = null;
    expect(text(fx, "j:signals.daily")).toMatch(/running now/);
  });

  it("names what it pulls, writes and publishes", () => {
    const t = text(FIXTURE, "j:ingest.bars_daily");
    expect(t).toContain("It pulls the Prices feed");
    expect(t).toContain("writes 3 series");
    expect(refsOf(read(FIXTURE, "j:farm.sweep"))).toContain("p:farm.sweep");
  });

  it("says it has not run when no run of its kind is in the last day, and leaves out a run older than that", () => {
    expect(text(FIXTURE, "j:regime.hmm")).toContain("It has no run in the last day");
    const later = { runs: FIXTURE.runs, now: new Date("2026-10-29T03:30:00Z") };   // the 01:30Z league run is now over a day old
    expect(flatten(read(FIXTURE, "j:vol.forecast_league", later))).toContain("In the last day it ran once, which failed, for 197 CPU-seconds");
  });

  it("does not count a run cancelled while queued, and dates a run with no start by its end", () => {
    const fx = clone();
    fx.runs.push(
      { id: "x1", kind: "regime.hmm", state: "cancelled", start: null, end: "2026-10-28T20:00:00Z", cpu: null, progress: null, msg: null },
      { id: "x2", kind: "farm.sweep", state: "done", start: null, end: "2026-10-27T10:00:00Z", cpu: 9, progress: 1, msg: null },
      { id: "x3", kind: "farm.sweep", state: "done", start: null, end: null, cpu: 4, progress: 1, msg: null },
    );
    expect(text(fx, "j:regime.hmm")).toContain("It has no run in the last day");
    // x2 ended over a day ago; x3, seen only through the job stream, has no times yet and counts
    expect(text(fx, "j:farm.sweep")).toContain("In the last day it ran 2 times for 5,236 CPU-seconds");
  });
});

describe("reading: the farm", () => {
  it("gives the cells with a result, the trials, the best Sharpe and its DSR, how many pass, and the farm's strategies", () => {
    const r = read(FIXTURE, "p:farm.sweep");
    const t = flatten(r);
    expect(t).toContain("2 cells with a result");
    expect(t).toContain("48 trials");
    expect(t).toMatch(/best out-of-sample Sharpe is 0\.81, from tsmom on us equity indices, with a DSR of 0\.97/);
    expect(t).toContain("1 of the 2 cells passes every gate");
    expect(t).toContain("1 of the desk's 2 strategies is a farm cell, at 2.5% of capital");
    expect(refsOf(r)).toContain("c:tsmom@us_equity_indices");
  });

  it("names a best cell that has no node in words, never by its raw id", () => {
    const fx = clone();
    const lens = nodeOf(fx, "p:farm.sweep").m.lens as { board: unknown[][] };
    lens.board[0][0] = "low_vol@sector_etfs";
    const r = read(fx, "p:farm.sweep");
    expect(flatten(r)).toContain("from low vol on sector etfs, with a DSR of 0.97");
    expect(flatten(r)).not.toMatch(/c:|@|low_vol/);
    expect(refsOf(r)).not.toContain("c:low_vol@sector_etfs");
  });

  it("says no cell passes when none does", () => {
    const fx = clone();
    const lens = nodeOf(fx, "p:farm.sweep").m.lens as { board: unknown[][] };
    lens.board.forEach((row) => (row[1] = "FAIL"));
    expect(text(fx, "p:farm.sweep")).toContain("No cell passes");
  });

  it("says in plain words that the board's figures are not given when its lens could not be read", () => {
    const t = text(FIXTURE_BROKEN, "p:farm.sweep");
    expect(t).toContain("Its leaderboard is not given here. This figure could not be read from the latest run.");
    expect(t).not.toMatch(/Sharpe is|DSR of|passes/);
  });

  it("quotes the farm's own notes as its caveats", () => {
    expect(read(FIXTURE, "p:farm.sweep").caveats).toEqual(nodeOf(FIXTURE, "p:farm.sweep").m.notes);
  });
});

describe("reading: daily signals", () => {
  it("the daily-signals caveats are quoted", () => {
    const notes = nodeOf(FIXTURE, "p:signals.daily").m.notes as string[];
    expect(read(FIXTURE, "p:signals.daily").caveats).toEqual([notes[1], notes[2]]);
  });

  it("gives the counts parsed from its first note", () => {
    const t = text(FIXTURE, "p:signals.daily");
    expect(t).toContain("wrote 0 new documents and 1 again");
    expect(t).toContain("held 17");
    expect(t).toContain("refused 3");
    expect(t).not.toMatch(/\blate\b|already/);
  });

  it("finds the two execution notes by what they say, not by a stray word, in any order", () => {
    const fx = clone();
    const node = nodeOf(fx, "p:signals.daily");
    const [first, lev, fill] = node.m.notes as string[];
    const stray = "an open question about the close of the books";
    node.m.notes = [first, stray, fill, lev];
    expect(executionNotes(node.m.notes as string[])).toEqual([lev, fill]);
    expect(text(fx, "p:signals.daily")).toContain("It notes 2 ways the farm's tests differ from what the desk trades.");
  });

  it("quotes every note it did not state, a desk that did not answer among them", () => {
    const fx = clone();
    const node = nodeOf(fx, "p:signals.daily");
    const [first, lev, fill] = node.m.notes as string[];
    const desk = "desk unreachable: keyed on the last written document (OHCAMEL_QUANT_LIVE_ENGINE_URL is unset)";
    const withheld = "A note from this job holds internal detail and is not shown here.";
    node.m.notes = [first, desk, withheld, lev, fill];
    expect(read(fx, "p:signals.daily").caveats).toEqual([desk, withheld, lev, fill]);
  });

  it("quotes every note when its counts note does not parse, and states no count", () => {
    const fx = clone();
    const node = nodeOf(fx, "p:signals.daily");
    const notes = ["desk unreachable: keyed on the last written document", ...(node.m.notes as string[]).slice(1)];
    node.m.notes = notes;
    const r = read(fx, "p:signals.daily");
    expect(r.caveats).toEqual(notes);
    expect(flatten(r)).not.toMatch(/wrote|held|refused/);
  });
});

describe("reading: a stale feed", () => {
  it("says its data is older than its schedule allows, by the feed's date and with no word of an artifact", () => {
    const fx = clone();
    nodeOf(fx, "feed:prices").m.stale = true;
    const t = text(fx, "feed:prices");
    expect(t).toContain("Its newest data is from 28 Oct, older than its schedule allows.");
    expect(t).not.toMatch(/artifact|run/);
    expect(text(FIXTURE, "feed:prices")).not.toMatch(/older|schedule/);
  });

  it("says so without a date when the feed has none", () => {
    const fx = clone();
    Object.assign(nodeOf(fx, "feed:prices").m, { stale: true, asof: null });
    expect(text(fx, "feed:prices")).toContain("Its newest data is older than its schedule allows.");
  });
});

describe("reading: other products", () => {
  it("gives the vol league's best-set rates", () => {
    const t = text(FIXTURE, "p:vol.forecast_league");
    expect(t).toContain("HAR");
    expect(t).toContain("HAR stays in the statistical best set for 61.2% of them");
    expect(t).toContain("49 names");
    expect(t).toContain("Verdict: DESCRIPTIVE ONLY");
  });

  it("gives the regime model's probability now, as of its date", () => {
    const t = text(FIXTURE, "p:regime.hmm");
    expect(t).toContain("0.18");
    expect(t).toContain("23 Oct");
    expect(t).toContain("436 weeks");
  });

  it("gives desk.report its run times, and of its contents only that the book summary is shown on the book (spec §7 Q1)", () => {
    const r = read(FIXTURE, "p:desk.report");
    expect(flatten(r)).toContain("Its latest report finished at 4:30 PM on 28 Oct. Of what it reports, this page shows only the book summary, on the book.");
    expect(refsOf(r)).toEqual(["book"]);
    expect(flatten(r)).not.toMatch(/research pages|only when it ran|fill|P&L|refus|position/i);
    // with no summary on the book, it says nothing of its contents at all
    const none = read(FIXTURE_NO_SUMMARY, "p:desk.report");
    expect(flatten(none)).toBe("Desk report\nIts latest report finished at 4:30 PM on 28 Oct.");
    expect(refsOf(none)).toEqual([]);
  });

  it("gives the research service its strategies and no verdict", () => {
    const r = read(FIXTURE, "p:research");
    expect(flatten(r)).toContain("1 of the desk's 2 strategies");
    expect(flatten(r)).not.toMatch(/verdict|PASS|FAIL/i);
    expect(refsOf(r)).toContain("st:exp_a01_spy");
  });

  it("says a missing product is missing, and why, and nothing else", () => {
    const fx = clone();
    const node = nodeOf(fx, "p:regime.hmm");
    node.status = "missing";
    node.why = "Its job has not published anything yet.";
    node.m = { kind: "regime.hmm" };
    const r = read(fx, "p:regime.hmm");
    expect(r.paras).toHaveLength(1);
    expect(flatten(r)).toContain("This is missing from the machine. Its job has not published anything yet.");
    expect(numbersIn(r.paras.flat().map((s) => ("b" in s ? s.b : s.t)).join(""))).toEqual([]);
    expect(r.caveats).toEqual([]);
  });

  it("writes a date or instant inside a server's reason as the page writes times, never as raw ISO text", () => {
    const fx = clone();
    const node = nodeOf(fx, "p:regime.hmm");
    node.status = "missing";
    node.why = "The last run is from 2026-10-24T13:07:33.610Z, on data as of 2026-10-23.";
    node.m = { kind: "regime.hmm" };
    expect(text(fx, "p:regime.hmm")).toContain("The last run is from 9:07 AM on 24 Oct, on data as of 23 Oct.");
  });

  it("keeps the year of a date or instant in a reason when it is not the payload's own year", () => {
    const fx = clone();
    nodeOf(fx, "c:xsmom@sectors").m = { status: "skipped", reason: "the universe starts 2019-01-02; last run 2025-12-31T15:00:00Z; bad 2026-13-45", combos: 24 };
    const t = text(fx, "c:xsmom@sectors");
    expect(t).toContain("the universe starts 2 Jan 2019; last run 10:00 AM on 31 Dec 2025; bad 2026-13-45.");
    const allowed = allowedNumbers(fx);
    for (const n of numbersIn(t)) expect(allowed, n).toContain(n);
  });

  it("says a scheduled run has been missed since a stale product, and only then", () => {
    const fx = clone();
    nodeOf(fx, "p:regime.hmm").m.stale = true;
    expect(text(fx, "p:regime.hmm")).toContain("A scheduled run has been missed since.");
    expect(text(FIXTURE, "p:regime.hmm")).not.toContain("missed");
  });

  it("gives any other product its notes, verbatim", () => {
    const fx = clone();
    fx.nodes.push({ id: "p:kronos.shadow", k: "product", l: "K", n: "Kronos shadow", ring: "products", at: {},
      m: { kind: "kronos.shadow", verdict: "FAIL", detail: null, notes: ["scored on days it has never seen"], stale: false } });
    const r = read(fx, "p:kronos.shadow");
    expect(r.caveats).toEqual(["scored on days it has never seen"]);
    expect(flatten(r)).toContain("Verdict: FAIL");
  });
});

describe("reading: lenses the fixture does not carry", () => {
  const fx = clone();
  const prod = (kind: string, n: string, m: Record<string, unknown>): MNode =>
    ({ id: `p:${kind}`, k: "product", l: kind, n, ring: "products", at: {}, m: { kind, verdict: null, detail: null, notes: [], stale: false, ...m } });
  fx.nodes.push(
    prod("cov.league", "Covariance league", { lens: { estimators: [
      ["lw_identity", "ndx100", 0.148, 1], ["sample", "ndx100", 0.16, 4], ["oas", "sectors", 0.121, 1], ["sample", "sectors", 0.13, 3],
    ] } }),
    prod("risk.mc_atlas", "Risk atlas", { lens: { measures: [["fhs", 1, 0.0182, 0.0231, 1000000], ["fhs", 10, 0.0421, 0.0538, 1000000], ["copula_t", 10, 0.0455, 0.0577, 1000000]] } }),
    prod("models.xs_lgbm", "Cross-section model", { lens: { deciles: [[1, 0.0042], [2, 0.0119], [3, 0.0071], [4, 0.0089]] } }),
    prod("vol.surface_history", "Vol surface history", { lens: { underlying: "SPY", asof: "2026-10-28", atm30: 0.1617, slices: 8, names: 30 } }),
    { id: "am:fhs:10", k: "result", l: "FHS 10-day", n: "FHS 10-day VaR and ES at 99%", ring: "results", at: {},
      m: { var: 0.0421, es: 0.0538, var_usd: 42100, es_usd: 53800, paths: 1000000 } },
    { id: "lm:har", k: "result", l: "HAR", n: "HAR volatility model", ring: "results", at: {}, m: { mcs_rate: 0.6122, mean_rank: 1.84, names: 49 } },
    { id: "ce:oas", k: "result", l: "oas", n: "oas covariance estimator", ring: "results", at: {}, m: { sectors: { vol: 0.121, rank: 1 } } },
  );

  it("gives each lens a sentence from its own figures", () => {
    expect(text(fx, "p:cov.league")).toMatch(/lw identity on ndx100 \(14\.8% realized volatility\).*oas on sectors \(12\.1%\)/);
    const atlas = text(fx, "p:risk.mc_atlas");
    expect(atlas).toContain("10-day");
    expect(atlas).toContain("4.21%");
    expect(atlas).toContain("5.38%");
    expect(atlas).toContain("1,000,000 paths");
    expect(atlas).not.toContain("1.82%");
    expect(text(fx, "p:models.xs_lgbm")).toMatch(/top decile.*0\.89%.*bottom.*0\.42%.*do not climb/);
    const surface = text(fx, "p:vol.surface_history");
    expect(surface).toContain("30 underlyings");
    expect(surface).toContain("16.2%");
    expect(surface).toContain("8 expiry slices");
    expect(surface).not.toMatch(/contract/);
  });

  it("gives the other results their own figures", () => {
    expect(text(fx, "am:fhs:10")).toMatch(/4\.21%.*5\.38%.*1,000,000 paths/);
    expect(text(fx, "lm:har")).toMatch(/61\.2% of 49 names/);
    expect(text(fx, "ce:oas")).toMatch(/sectors.*12\.1%.*rank 1/);
  });

  it("is honest on these lenses too", () => {
    const gi = indexGraph(fx), allowed = allowedNumbers(fx);
    fx.nodes.forEach((node, i) => {
      const t = flatten(reading(gi, i, ctxOf(fx)));
      expect(t).not.toMatch(/NaN|undefined|null|—/);
      for (const n of numbersIn(t)) expect(allowed, `${node.id}: ${n}`).toContain(n);
    });
  });
});

describe("reading: results and strategies", () => {
  it("gives a farm cell its variants, Sharpe, PSR, DSR, PBO, verdict and detail, and the strategy it becomes", () => {
    const r = read(FIXTURE, "c:tsmom@us_equity_indices");
    const t = flatten(r);
    for (const s of ["24 parameter variants", "0.81", "0.99", "0.97", "0.21", "48 trials", "PASS", "ALL GATES PASS", "2.5% of capital"]) expect(t).toContain(s);
    expect(refsOf(r)).toContain("st:farm_tsmom_us_equity_indices");
    const x = text(FIXTURE, "c:xsmom@sectors");
    expect(x).toContain("DEFLATED SHARPE BELOW 0.95");
    expect(x).not.toMatch(/trades as/);
  });

  it("says a skipped cell has no result, and why", () => {
    const fx = clone();
    nodeOf(fx, "c:xsmom@sectors").m = { status: "skipped", reason: "the universe has too short a history", combos: 24 };
    expect(text(fx, "c:xsmom@sectors")).toContain("no result from the latest run: the universe has too short a history");
  });

  it("gives a strategy its capital, symbols, producer and farm cell", () => {
    const farm = read(FIXTURE, "st:farm_tsmom_us_equity_indices");
    expect(flatten(farm)).toContain("2.5% of capital");
    expect(flatten(farm)).toContain("SPY, QQQ, DIA, IWM and MDY");
    expect(refsOf(farm)).toEqual(expect.arrayContaining(["p:signals.daily", "c:tsmom@us_equity_indices", "d:SPY"]));
    expect(farm.title).toBe("tsmom us equity indices");
    const research = read(FIXTURE, "st:exp_a01_spy");
    expect(refsOf(research)).toContain("p:research");
    expect(flatten(research)).not.toMatch(/trades /);
  });
});

describe("reading: the book, its limits and kill switch", () => {
  it("the book without a summary says why and names no limit", () => {
    const r = read(FIXTURE_NO_SUMMARY, "book");
    const why = nodeOf(FIXTURE_NO_SUMMARY, "book").m.summary_why as string;
    expect(flatten(r)).toContain(why);
    expect(flatten(r)).not.toMatch(/positions|armed|halted|aapl-cap|dd-cap/);
    expect(refsOf(r).some((id) => id.startsWith("lim:") || id === "kill")).toBe(false);
  });

  it("gives the book its strategies and capital, and with a summary its positions, limits and kill switch as of the summary", () => {
    const r = read(FIXTURE, "book");
    const t = flatten(r);
    expect(t).toContain("2 strategies send it signals, 1 from the research service and 1 from daily signals.");
    expect(t).toContain("Their allocations add up to 5% of capital, under a 60% incubation cap.");
    expect(t).toContain("10% volatility target");
    expect(t).toContain("As of 4:30 PM on 28 Oct, the desk report's book summary shows 14 positions, 2 limits (aapl-cap and dd-cap) and the kill switch armed.");
    expect(refsOf(r)).toEqual(expect.arrayContaining(["lim:aapl-cap", "lim:dd-cap", "kill", "p:signals.overlay"]));
  });

  it("says the live book only from a summary's time, and nothing of limits with neither a summary nor its reason", () => {
    const fx = clone();
    delete nodeOf(fx, "book").m.summary_asof;
    expect(text(fx, "book")).not.toMatch(/positions|limit|kill/);
    const stale = structuredClone(FIXTURE_NO_SUMMARY) as MachineOut;
    nodeOf(stale, "book").m.summary_why = "The last desk report is from 2026-10-28T20:30:44.907Z, and a scheduled one has been missed since.";
    expect(text(stale, "book")).toContain("The last desk report is from 4:30 PM on 28 Oct, and a scheduled one has been missed since.");
    expect(text(stale, "book")).not.toMatch(/system's reason|“/);
  });

  it("gives a limit its kind, scope and whether it trips the kill switch, as of the summary, and never a threshold", () => {
    const dd = text(FIXTURE, "lim:dd-cap");
    expect(dd).toMatch(/max drawdown limit on the whole portfolio/);
    expect(dd).toMatch(/trips the kill switch/);
    expect(dd).toContain("4:30 PM on 28 Oct");
    expect(dd).toMatch(/threshold is not public/);
    const aapl = read(FIXTURE, "lim:aapl-cap");
    expect(flatten(aapl)).toMatch(/gross notional limit on AAPL alone/);
    expect(flatten(aapl)).toMatch(/does not trip the kill switch/);
    expect(refsOf(aapl)).toContain("d:AAPL");
  });

  it("gives the kill switch its state and the limits that trip it, as of the summary", () => {
    const r = read(FIXTURE, "kill");
    expect(flatten(r)).toContain("armed");
    expect(flatten(r)).toContain("4:30 PM on 28 Oct");
    expect(refsOf(r)).toEqual(["lim:dd-cap"]);
  });
});

describe("reading: robustness", () => {
  it("is empty for an index that is not a node", () => {
    const gi = indexGraph(FIXTURE);
    for (const i of [-1, FIXTURE.nodes.length, 1.5, NaN]) expect(reading(gi, i, ctxOf(FIXTURE))).toEqual({ title: "", paras: [], caveats: [] });
  });

  it("never throws or prints a gap when every node's fields are missing", () => {
    const fx = clone();
    fx.nodes.forEach((n) => (n.m = {}));
    const gi = indexGraph(fx);
    fx.nodes.forEach((node, i) => {
      const t = flatten(reading(gi, i, { runs: [], now: new Date(fx.asof) }));
      expect(t, node.id).not.toMatch(/NaN|undefined|null|—/);
    });
  });
});
