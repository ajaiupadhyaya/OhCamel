import { describe, expect, it } from "vitest";
import { dayRuns, heaviest, REPLAY_S, replayFrame, replayHold, runningRuns } from "./day";
import { FIXTURE } from "./fixture";
import { jobRuns } from "./prose";
import type { Run } from "./types";

/** The fixture's own moment: 18:51 on 28 Oct in New York. */
const NOW = new Date(FIXTURE.asof);
const S = 1000, H = 3_600_000, DAY = 24 * H;
const FROM = NOW.getTime() - DAY;
const iso = (t: number): string => new Date(t).toISOString();
/** An instant `ms` before NOW, as the wire writes it. */
const ago = (ms: number): string => iso(NOW.getTime() - ms);
const run = (id: string, over: Partial<Run> = {}): Run => ({
  id, kind: "k", state: "done", start: ago(2 * H), end: ago(H), cpu: 1, progress: 1, msg: null, ...over,
});
const ids = (rs: Run[]): string[] => rs.map((r) => r.id);

describe("REPLAY_S", () => {
  it("is 26 seconds", () => {
    expect(REPLAY_S).toBe(26);
  });
});

describe("dayRuns: which runs, in what order", () => {
  it("lists a running run first, then a queued one, then the finished runs newest first", () => {
    const out = dayRuns(FIXTURE.runs, NOW);
    expect(out).toHaveLength(FIXTURE.runs.length);
    expect(ids(out)).toEqual([
      "01M62B0BB49PABRJV2BRWE853B",   // signals.daily, running
      "01M62B9GA4XD47RXN7Z58E12E6",   // signals.overlay, queued behind it
      "01M62AQ6C4CPX3DEJZDNW4GCA5",   // signals.overlay, done 22:40
      "01M62A4WE49XXBSPV775XG3DNE",   // ingest.bars_daily, done 22:30
      "01M62395641CESN081A6974NQ3",   // desk.report, done 20:30
      "01M61TT5A4PF1MN8TVBQADPFW8",   // vol.forecast_league, failed 18:02
      "01M6076HR4Y1Y3G4CZQVWJEXHY",   // farm.sweep, done 03:00
      "01M6021RA4P60KNJAPA96SPAVR",   // vol.forecast_league, done 01:30
    ]);
  });

  it("returns the very runs it was given, in a new array, and does not change the input", () => {
    const before = structuredClone(FIXTURE.runs);
    const out = dayRuns(FIXTURE.runs, NOW);
    expect(out).not.toBe(FIXTURE.runs);
    out.forEach((r) => expect(FIXTURE.runs).toContain(r));
    expect(FIXTURE.runs).toEqual(before);
  });

  it("returns nothing for no runs", () => {
    expect(dayRuns([], NOW)).toEqual([]);
  });

  describe("the 24 hour window (the readings' rule)", () => {
    it("keeps a finished run that started exactly 24 h ago and drops one that started a millisecond earlier", () => {
      const inside = run("in", { start: iso(FROM), end: iso(FROM + H) });
      const outside = run("out", { start: iso(FROM - 1), end: iso(FROM + H) });
      expect(ids(dayRuns([outside, inside], NOW))).toEqual(["in"]);
    });

    it("dates a run by its start, even when it ended inside the window", () => {
      expect(dayRuns([run("straddle", { start: ago(25 * H), end: ago(23 * H) })], NOW)).toEqual([]);
    });

    it("keeps a run that started just now, and one dated ahead of this clock (the server's clock may lead)", () => {
      expect(ids(dayRuns([run("now", { start: iso(NOW.getTime()) }), run("ahead", { start: iso(NOW.getTime() + 5 * S) })], NOW))).toEqual(["ahead", "now"]);
    });

    it("dates a run with no start by its end", () => {
      expect(ids(dayRuns([run("e-in", { start: null, end: ago(H) }), run("e-out", { start: null, end: ago(30 * H) })], NOW))).toEqual(["e-in"]);
    });

    it("keeps a finished run whose times are not known yet, or do not read", () => {
      const none = run("none", { state: "failed", start: null, end: null });
      const junk = run("junk", { start: "not a time", end: null });
      expect(ids(dayRuns([none, junk], NOW)).sort()).toEqual(["junk", "none"]);
    });

    it("reads a timestamp with no offset as UTC, as the readings do", () => {
      const naive = run("naive", { start: new Date(FROM + H).toISOString().slice(0, 19), end: null, state: "failed" });
      const early = run("early", { start: new Date(FROM - H).toISOString().slice(0, 19), end: null, state: "failed" });
      expect(ids(dayRuns([naive, early], NOW))).toEqual(["naive"]);
    });

    it("leaves out a run cancelled while it was still queued, which never ran, and keeps one cancelled after it started", () => {
      const never = run("never", { state: "cancelled", start: null, end: ago(H), cpu: null, progress: null });
      const started = run("started", { state: "cancelled", start: ago(2 * H), end: ago(H) });
      expect(ids(dayRuns([never, started], NOW))).toEqual(["started"]);
    });
  });

  describe("queued and running runs (ruling 17: by state, never by start)", () => {
    it("lists a run still running whatever its start, even one that began more than a day ago", () => {
      const old = run("old", { state: "running", start: ago(40 * H), end: null });
      expect(ids(dayRuns([old], NOW))).toEqual(["old"]);
    });

    it("lists a run that has just started and has no start yet", () => {
      const fresh = run("fresh", { state: "running", start: null, end: null, progress: 0 });
      const older = run("older", { state: "running", start: ago(H), end: null });
      expect(ids(dayRuns([older, fresh], NOW))).toEqual(["fresh", "older"]);   // no start yet means newest
    });

    it("lists queued runs, which have no start, whatever the window", () => {
      expect(ids(dayRuns([run("q", { state: "queued", start: null, end: null, cpu: null, progress: null })], NOW))).toEqual(["q"]);
    });

    it("puts the running runs above the queued, and both above every finished run, however new that one is", () => {
      const out = dayRuns([
        run("done-new", { start: ago(S), end: ago(S / 2) }),
        run("queued", { state: "queued", start: null, end: null }),
        run("running", { state: "running", start: ago(10 * H), end: null }),
        run("done-old", { start: ago(23 * H), end: ago(22 * H) }),
      ], NOW);
      expect(ids(out)).toEqual(["running", "queued", "done-new", "done-old"]);
    });
  });

  describe("order within a group", () => {
    it("sorts the finished runs by start, newest first, and dates one with no start by its end", () => {
      const out = dayRuns([
        run("a", { start: ago(5 * H), end: ago(4 * H) }),
        run("c", { start: ago(H), end: ago(H / 2) }),
        run("b", { start: null, end: ago(3 * H) }),
      ], NOW);
      expect(ids(out)).toEqual(["c", "b", "a"]);
    });

    it("breaks a tie by id, the later id (a ULID) first, so the order is the same on every call", () => {
      const t = { start: ago(2 * H), end: ago(H) };
      const a = run("01A", t), b = run("01B", t), c = run("01C", t);
      expect(ids(dayRuns([a, c, b], NOW))).toEqual(["01C", "01B", "01A"]);
      expect(ids(dayRuns([b, a, c], NOW))).toEqual(["01C", "01B", "01A"]);
    });
  });

  describe("agrees with the readings on which runs fall in the last day", () => {
    const MIXED: Run[] = [
      ...FIXTURE.runs,
      run("c1", { kind: "farm.sweep", state: "cancelled", start: null, end: ago(H), cpu: null }),
      run("c2", { kind: "farm.sweep", state: "cancelled", start: ago(2 * H), end: ago(2 * H - 10 * S) }),
      run("n1", { kind: "x", start: null, end: ago(30 * H) }),
      run("n2", { kind: "x", start: null, end: ago(H) }),
      run("n3", { kind: "x", state: "failed", start: null, end: null }),
      run("n4", { kind: "x", start: "garbage", end: ago(H) }),
      run("n5", { kind: "x", state: "failed", start: ago(24 * H), end: ago(23 * H) }),
      run("n6", { kind: "x", state: "failed", start: ago(24 * H + 1), end: ago(23 * H) }),
      run("old", { kind: "x", state: "running", start: ago(40 * H), end: null }),
      run("q", { kind: "x", state: "queued", start: null, end: null }),
    ];
    const kinds = [...new Set(MIXED.map((r) => r.kind))];
    const sorted = (rs: Run[]): string[] => ids(rs).sort();

    it.each([
      ["now", NOW],
      ["3 h later", new Date(NOW.getTime() + 3 * H)],
      ["10 h earlier", new Date(NOW.getTime() - 10 * H)],
      ["30 h later", new Date(NOW.getTime() + 30 * H)],
      ["1 ms later", new Date(NOW.getTime() + 1)],
    ])("at %s, run for run, kind by kind", (_name, now) => {
      const day = dayRuns(MIXED, now);
      expect(new Set(ids(day)).size).toBe(day.length);
      for (const kind of kinds) {
        const mine = day.filter((r) => r.kind === kind);
        const { ran, running, queued } = jobRuns(MIXED, kind, now);
        expect(sorted(mine.filter((r) => r.state === "done" || r.state === "failed" || r.state === "cancelled"))).toEqual(sorted(ran));
        expect(sorted(mine.filter((r) => r.state === "running"))).toEqual(sorted(running));
        expect(sorted(mine.filter((r) => r.state === "queued"))).toEqual(sorted(queued));
      }
      // and nothing is listed that the readings would not count
      expect(day.length).toBe(kinds.reduce((s, k) => {
        const j = jobRuns(MIXED, k, now);
        return s + j.ran.length + j.running.length + j.queued.length;
      }, 0));
    });
  });
});

describe("heaviest", () => {
  it("is the done run with the most CPU-seconds in the last day", () => {
    const h = heaviest(FIXTURE.runs, NOW);
    expect(h?.id).toBe("01M6076HR4Y1Y3G4CZQVWJEXHY");
    expect(h?.kind).toBe("farm.sweep");
    expect(h?.cpu).toBe(5231.8);
  });

  it("ignores a failed run, however much CPU it used", () => {
    const rs = [run("ok", { cpu: 10 }), run("bad", { state: "failed", cpu: 9999 })];
    expect(heaviest(rs, NOW)?.id).toBe("ok");
    expect(heaviest([run("bad", { state: "failed", cpu: 9999 })], NOW)).toBeNull();
  });

  it("ignores a cancelled, a running and a queued run, whatever CPU they carry", () => {
    const rs = [
      run("c", { state: "cancelled", cpu: 500 }),
      run("r", { state: "running", end: null, cpu: 600 }),
      run("q", { state: "queued", start: null, end: null, cpu: 700 }),
      run("d", { cpu: 5 }),
    ];
    expect(heaviest(rs, NOW)?.id).toBe("d");
  });

  it("ignores a done run with no CPU figure, or one that is not a finite number", () => {
    const rs = [run("nul", { cpu: null }), run("nan", { cpu: Number.NaN }), run("inf", { cpu: Infinity }), run("one", { cpu: 1 })];
    expect(heaviest(rs, NOW)?.id).toBe("one");
    expect(heaviest(rs.slice(0, 3), NOW)).toBeNull();
  });

  it("ignores a run from before the last day, by the same window the list uses", () => {
    const rs = [run("old", { start: ago(25 * H), end: ago(24 * H + 1), cpu: 9000 }), run("new", { cpu: 3 })];
    expect(heaviest(rs, NOW)?.id).toBe("new");
    expect(heaviest([rs[0]], NOW)).toBeNull();
    // the window's edge, as dayRuns draws it
    expect(heaviest([run("edge", { start: iso(FROM), cpu: 4 })], NOW)?.id).toBe("edge");
    expect(heaviest([run("past", { start: iso(FROM - 1), cpu: 4 })], NOW)).toBeNull();
  });

  it("counts a done run whose start is not known yet, dated by its end", () => {
    expect(heaviest([run("e", { start: null, end: ago(H), cpu: 8 })], NOW)?.id).toBe("e");
    expect(heaviest([run("e", { start: null, end: ago(30 * H), cpu: 8 })], NOW)).toBeNull();
  });

  it("counts a run of zero CPU-seconds, which is a figure", () => {
    expect(heaviest([run("z", { cpu: 0 })], NOW)?.id).toBe("z");
  });

  it("breaks a tie for the newer run, then the later id", () => {
    const a = run("A", { cpu: 7, start: ago(5 * H), end: ago(4 * H) });
    const b = run("B", { cpu: 7, start: ago(2 * H), end: ago(H) });
    expect(heaviest([a, b], NOW)?.id).toBe("B");
    expect(heaviest([b, a], NOW)?.id).toBe("B");
    const c = run("C", { cpu: 7, start: ago(2 * H), end: ago(H) });
    expect(heaviest([b, c], NOW)?.id).toBe("C");
    expect(heaviest([c, b], NOW)?.id).toBe("C");
  });

  it("is null for no runs, and for a day with no done run", () => {
    expect(heaviest([], NOW)).toBeNull();
    expect(heaviest(FIXTURE.runs.filter((r) => r.state !== "done"), NOW)).toBeNull();
  });

  it("returns one of the runs it was given", () => {
    expect(FIXTURE.runs).toContain(heaviest(FIXTURE.runs, NOW));
  });
});

describe("runningRuns", () => {
  it("is the runs whose state is running, in the order given", () => {
    expect(ids(runningRuns(FIXTURE.runs))).toEqual(["01M62B0BB49PABRJV2BRWE853B"]);
    const rs = [run("a", { state: "running", end: null }), run("b"), run("c", { state: "running", start: null, end: null }), run("d", { state: "queued", start: null, end: null })];
    expect(ids(runningRuns(rs))).toEqual(["a", "c"]);
  });

  it("keys on state, not start: a run that just started has no start and is running", () => {
    expect(ids(runningRuns([run("fresh", { state: "running", start: null, end: null })]))).toEqual(["fresh"]);
    expect(runningRuns([run("done-no-end", { start: ago(H), end: null })])).toEqual([]);
  });

  it("is empty for no runs, and does not change its input", () => {
    expect(runningRuns([])).toEqual([]);
    const before = structuredClone(FIXTURE.runs);
    runningRuns(FIXTURE.runs);
    expect(FIXTURE.runs).toEqual(before);
  });
});

describe("replayFrame", () => {
  it("sweeps the day from 24 h ago (0) to now (1)", () => {
    expect(replayFrame(FIXTURE.runs, NOW, 0).at.getTime()).toBe(FROM);
    expect(replayFrame(FIXTURE.runs, NOW, 1).at.getTime()).toBe(NOW.getTime());
    expect(replayFrame(FIXTURE.runs, NOW, 0.5).at.getTime()).toBe(FROM + 12 * H);
    expect(replayFrame(FIXTURE.runs, NOW, 0.25).at.getTime()).toBe(FROM + 6 * H);
  });

  it("puts the hand on the nearest whole millisecond", () => {
    expect(replayFrame([], NOW, 0.4 / DAY).at.getTime()).toBe(FROM);
    expect(replayFrame([], NOW, 0.6 / DAY).at.getTime()).toBe(FROM + 1);
    expect(replayFrame([], NOW, 0.009).at.getTime()).toBe(FROM + 777_600);
  });

  it("keeps a fraction outside 0..1, or not a number, inside the day", () => {
    expect(replayFrame([], NOW, -0.5).at.getTime()).toBe(FROM);
    expect(replayFrame([], NOW, 7).at.getTime()).toBe(NOW.getTime());
    expect(replayFrame([], NOW, Infinity).at.getTime()).toBe(NOW.getTime());
    expect(replayFrame([], NOW, -Infinity).at.getTime()).toBe(FROM);
    expect(replayFrame([], NOW, Number.NaN).at.getTime()).toBe(FROM);
  });

  it("at the start of the day, lights only a run that began that very instant", () => {
    const rs = [
      run("first", { start: iso(FROM), end: iso(FROM + H) }),
      run("later", { start: iso(FROM + 2 * H), end: iso(FROM + 3 * H) }),
    ];
    expect(ids(replayFrame(rs, NOW, 0).active)).toEqual(["first"]);
    expect(replayFrame(FIXTURE.runs, NOW, 0).active).toEqual([]);
  });

  it("at the end of the day, lights nothing that has ended, and no run still going", () => {
    const f = replayFrame(FIXTURE.runs, NOW, 1);
    expect(f.at.getTime()).toBe(NOW.getTime());
    expect(f.active).toEqual([]);   // signals.daily is running: it has no end, so it is not among them
  });

  it("lights a run from its start up to, but not including, its end", () => {
    const at = FROM + 12 * H;   // frac 0.5 exactly
    const rs = [
      run("starts", { start: iso(at), end: iso(at + S) }),
      run("ends", { start: iso(at - S), end: iso(at) }),
      run("spans", { start: iso(at - H), end: iso(at + H) }),
      run("before", { start: iso(at - 2 * H), end: iso(at - H) }),
      run("after", { start: iso(at + H), end: iso(at + 2 * H) }),
    ];
    expect(ids(replayFrame(rs, NOW, 0.5).active).sort()).toEqual(["spans", "starts"]);
  });

  it("lights a failed run, and a run cancelled after it started: they ran", () => {
    const at = FROM + 12 * H;
    const rs = [
      run("failed", { state: "failed", start: iso(at - S), end: iso(at + S) }),
      run("cancelled", { state: "cancelled", start: iso(at - S), end: iso(at + S) }),
    ];
    expect(ids(replayFrame(rs, NOW, 0.5).active).sort()).toEqual(["cancelled", "failed"]);
  });

  it("uses only runs with both a start and an end (ruling 17)", () => {
    const at = FROM + 12 * H;
    const rs = [
      run("running", { state: "running", start: iso(at - H), end: null }),
      run("no-start", { start: null, end: iso(at + H) }),
      run("queued", { state: "queued", start: null, end: null }),
      run("no-times", { state: "failed", start: null, end: null }),
      run("junk-end", { start: iso(at - H), end: "not a time" }),
      run("both", { start: iso(at - H), end: iso(at + H) }),
    ];
    expect(ids(replayFrame(rs, NOW, 0.5).active)).toEqual(["both"]);
  });

  it("lights a run that is still going by state, not by a stale end: a run marked running is never among them", () => {
    const at = FROM + 12 * H;
    const odd = run("odd", { state: "running", start: iso(at - H), end: iso(at + H) });
    expect(replayFrame([odd], NOW, 0.5).active).toEqual([]);
  });

  it("lights only runs the list shows: one that began before the window is not among them", () => {
    const straddle = run("straddle", { start: iso(FROM - H), end: iso(FROM + H) });
    expect(dayRuns([straddle], NOW)).toEqual([]);
    expect(replayFrame([straddle], NOW, 0).active).toEqual([]);
    expect(replayFrame([straddle], NOW, 0.001).active).toEqual([]);
  });

  it("lights each finished run of the day while the hand is inside it, and not after", () => {
    const finished = FIXTURE.runs.filter((r) => r.start && r.end);
    expect(finished).toHaveLength(6);
    for (const r of finished) {
      const s = Date.parse(r.start!), e = Date.parse(r.end!);
      const frac = (t: number): number => (t - FROM) / DAY;
      expect(ids(replayFrame(FIXTURE.runs, NOW, frac(s)).active)).toContain(r.id);
      expect(ids(replayFrame(FIXTURE.runs, NOW, frac((s + e) / 2)).active)).toContain(r.id);
      expect(ids(replayFrame(FIXTURE.runs, NOW, frac(e)).active)).not.toContain(r.id);
      expect(ids(replayFrame(FIXTURE.runs, NOW, frac(s - 1000)).active)).not.toContain(r.id);
    }
  });

  it("lights two runs at once when they overlap, newest first, and every one is a run it was given", () => {
    const at = FROM + 12 * H;
    const a = run("a", { start: iso(at - 3 * H), end: iso(at + H) });
    const b = run("b", { start: iso(at - H), end: iso(at + 3 * H) });
    const f = replayFrame([a, b], NOW, 0.5);
    expect(ids(f.active)).toEqual(["b", "a"]);
    expect(f.active[0]).toBe(b);
    expect(f.active[1]).toBe(a);
  });

  it("returns a new Date each time and does not change its input", () => {
    const before = structuredClone(FIXTURE.runs);
    const a = replayFrame(FIXTURE.runs, NOW, 0.5), b = replayFrame(FIXTURE.runs, NOW, 0.5);
    expect(a.at).not.toBe(b.at);
    expect(a.at.getTime()).toBe(b.at.getTime());
    expect(FIXTURE.runs).toEqual(before);
    expect(NOW.getTime()).toBe(Date.parse(FIXTURE.asof));
  });

  it("does not throw with no runs or a clock that does not read", () => {
    expect(replayFrame([], NOW, 0.5).active).toEqual([]);
    expect(() => replayFrame(FIXTURE.runs, new Date(Number.NaN), 0.5)).not.toThrow();
  });
});

describe("replayHold", () => {
  const first = run("01A", { start: ago(20 * H), end: ago(20 * H - 3 * S) });
  const second = run("01B", { start: ago(10 * H), end: ago(9 * H) });
  const all = [second, first];
  const frac = (msBeforeNow: number): number => 1 - msBeforeNow / DAY;

  it("is nothing before the first run of the day has started", () => {
    expect(replayHold(all, NOW, 0)).toBeNull();
    expect(replayHold(all, NOW, frac(21 * H))).toBeNull();
  });

  it("is a run from the moment it starts, inclusive", () => {
    expect(replayHold(all, NOW, frac(20 * H))?.id).toBe("01A");
  });

  it("holds a short run after it ends, until the next one starts", () => {
    const between = frac(15 * H);
    expect(replayFrame(all, NOW, between).active).toEqual([]);
    expect(replayHold(all, NOW, between)?.id).toBe("01A");
    expect(replayHold(all, NOW, frac(10 * H))?.id).toBe("01B");
  });

  it("holds the last run to the end of the sweep", () => {
    expect(replayHold(all, NOW, 1)?.id).toBe("01B");
  });

  it("moves to the later start when runs overlap", () => {
    const long = run("01L", { start: ago(12 * H), end: ago(2 * H) });
    const inside = run("01S", { start: ago(11 * H), end: ago(10.5 * H) });
    expect(replayHold([long, inside], NOW, frac(5 * H))?.id).toBe("01S");
  });

  it("breaks a tie of starts by the later id, so the choice never varies", () => {
    const a = run("01A", { start: ago(5 * H), end: ago(4 * H) });
    const b = run("01B", { start: ago(5 * H), end: ago(4 * H) });
    expect(replayHold([a, b], NOW, 1)?.id).toBe("01B");
    expect(replayHold([b, a], NOW, 1)?.id).toBe("01B");
  });

  it("holds no run that did not start inside the day", () => {
    const before = run("01O", { start: ago(25 * H), end: ago(23 * H) });
    const stillGoing = run("01G", { state: "running", start: ago(25 * H), end: null });
    const queued = run("01Q", { state: "queued", start: null, end: null });
    const unstarted = run("01C", { state: "cancelled", start: null, end: ago(H) });
    expect(replayHold([before, stillGoing, queued, unstarted], NOW, 1)).toBeNull();
  });

  it("holds a running job from its start: it is the last started, with no end", () => {
    const going = run("01R", { state: "running", start: ago(H), end: null });
    expect(replayFrame([going], NOW, 1).active).toEqual([]);
    expect(replayHold([first, going], NOW, frac(2 * H))?.id).toBe("01A");
    expect(replayHold([first, going], NOW, frac(H))?.id).toBe("01R");
    expect(replayHold([first, going], NOW, 1)?.id).toBe("01R");
  });

  it("holds a failed or cancelled run: it ran, and it took the time", () => {
    const failed = run("01F", { state: "failed", start: ago(3 * H), end: ago(2 * H) });
    expect(replayHold([failed], NOW, 1)?.id).toBe("01F");
  });

  it("holds a run whose end it does not know", () => {
    const open = run("01E", { start: ago(3 * H), end: null });
    expect(replayHold([open], NOW, 1)?.id).toBe("01E");
  });

  it("keeps `frac` inside the day, as replayFrame does", () => {
    expect(replayHold(all, NOW, -3)).toBeNull();
    expect(replayHold(all, NOW, 4)?.id).toBe("01B");
    expect(replayHold(all, NOW, Number.NaN)).toBeNull();
  });
});
