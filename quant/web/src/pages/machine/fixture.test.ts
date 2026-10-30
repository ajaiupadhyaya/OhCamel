import { describe, expect, it } from "vitest";
import { FIXTURE, FIXTURE_BROKEN, FIXTURE_NO_SUMMARY } from "./fixture";
import type { EdgeKind, MachineOut, MNode } from "./types";

// A copy of spec section 5's RULES (quant/src/ohcamel_quant/machine/graph.py): each edge kind and the
// (from-kind, to-kind) pairs it allows. The fixture is held to it so it cannot drift from the server.
const RULES: Record<EdgeKind, [string, string][]> = {
  pull: [["feed", "job"]],
  write: [["job", "series"]],
  publish: [["job", "product"]],
  result: [["product", "result"]],
  read: [["series", "job"], ["product", "job"], ["series", "limit"], ["book", "job"]],
  roster: [["result", "strategy"]],
  signal: [["product", "strategy"]],
  trade: [["strategy", "book"]],
  scale: [["product", "book"]],
  limit: [["limit", "book"]],
  trip: [["limit", "kill"]],
  halt: [["kill", "book"]],
};
const EDGE_KINDS = Object.keys(RULES) as EdgeKind[];

const VARIANTS: [string, MachineOut][] = [["FIXTURE", FIXTURE], ["FIXTURE_NO_SUMMARY", FIXTURE_NO_SUMMARY], ["FIXTURE_BROKEN", FIXTURE_BROKEN]];
const byId = (out: MachineOut, id: string): MNode => {
  const n = out.nodes.find((x) => x.id === id);
  if (!n) throw new Error(`no node ${id}`);
  return n;
};
const keys = (n: MNode): string[] => Object.keys(n.m).sort();
const kindOf = (out: MachineOut, k: MNode["k"]): MNode[] => out.nodes.filter((n) => n.k === k);

describe.each(VARIANTS)("%s is a well-formed graph", (_name, out) => {
  it("obeys spec section 5: every edge is one its kind's rule allows", () => {
    for (const [a, b, kind] of out.edges) {
      const pair: [string, string] = [out.nodes[a].k, out.nodes[b].k];
      expect(RULES[kind], `edge kind ${kind}`).toBeDefined();
      expect(RULES[kind].some(([f, t]) => f === pair[0] && t === pair[1]), `${out.nodes[a].id} -${kind}-> ${out.nodes[b].id} is ${pair.join(" to ")}`).toBe(true);
    }
  });

  it("has no dangling, self or repeated edge, and unique node ids", () => {
    const seen = new Set<string>();
    for (const [a, b, kind] of out.edges) {
      expect(out.nodes[a], `edge from ${a}`).toBeDefined();
      expect(out.nodes[b], `edge to ${b}`).toBeDefined();
      expect(a).not.toBe(b);
      const key = `${a}/${b}/${kind}`;
      expect(seen.has(key), key).toBe(false);
      seen.add(key);
    }
    expect(new Set(out.nodes.map((n) => n.id)).size).toBe(out.nodes.length);
  });

  it("is plain JSON, as it comes over the wire", () => {
    expect(JSON.parse(JSON.stringify(out))).toEqual(out);
  });

  it("puts every node in its kind's ring and anchors it the way the server does", () => {
    const ids = new Set(out.nodes.map((n) => n.id));
    const ring: Record<string, string[]> = {
      feed: ["feeds"], job: ["clock"], result: ["results"], strategy: ["strategies"], limit: ["limits"], kill: ["limits"], book: ["book"],
      series: ["daily", "filings", "minute", "options"], product: ["products"],
    };
    for (const n of out.nodes) {
      expect(ring[n.k], `${n.id} ring`).toContain(n.ring);
      if (n.k === "job") expect(n.at).toEqual({ h: n.at.h });
      if (n.k === "series" && n.ring === "daily") expect(n.at.sec, n.id).toBeDefined();
      if (n.ring === "filings" || n.ring === "minute" || n.ring === "options") expect(ids.has(n.at.of ?? ""), `${n.id} sits on ${n.at.of}`).toBe(true);
      if (n.k === "result") expect(ids.has(n.at.of ?? ""), `${n.id} sits in ${n.at.of}`).toBe(true);
      if (n.k === "product" && n.id !== "p:research") expect(ids.has(n.at.of ?? ""), `${n.id} is published by ${n.at.of}`).toBe(true);
    }
    expect(byId(out, "p:research").at).toEqual({ beside: "p:signals.daily" });
  });

  it("holds its runs to the jobs the graph draws, with a running run and a queued one that has not started", () => {
    const jobKinds = new Set(kindOf(out, "job").map((n) => n.m.kind));
    expect(new Set(out.runs.map((r) => r.id)).size).toBe(out.runs.length);
    for (const r of out.runs) expect(jobKinds.has(r.kind), `${r.id} is a ${r.kind}`).toBe(true);
    expect(out.runs.some((r) => r.state === "running")).toBe(true);
    const queued = out.runs.filter((r) => r.state === "queued");
    expect(queued.length).toBeGreaterThan(0);
    for (const r of queued) expect(r).toMatchObject({ start: null, end: null, cpu: null, progress: null });
    for (const r of out.runs.filter((x) => x.state === "done")) expect(r).toMatchObject({ progress: 1 });
    for (const r of out.runs.filter((x) => x.state === "running")) expect(r.end).toBeNull();
  });

  it("states its job hours as the clock does: a lone fire at its own hour, a run under 12 hours apart at its middle", () => {
    for (const j of kindOf(out, "job")) {
      const fires = j.m.fires as number[];
      const [first, last] = [Math.min(...fires), Math.max(...fires)];
      const want = last - first < 12 ? (first + last) / 2 : last;
      expect(j.at.h, j.id).toBeCloseTo(want, 3);
      expect(j.id).toBe(`j:${j.m.kind}`);
    }
  });

  it("lists every fire in x.fires, three places, by kind and then hour", () => {
    const want = kindOf(out, "job")
      .flatMap((j) => (j.m.fires as number[]).map((h): [string, number] => [j.m.kind as string, Math.round(h * 1000) / 1000]))
      .sort((a, b) => a[0].localeCompare(b[0]) || a[1] - b[1]);
    expect(out.x.fires).toEqual(want);
    expect(out.x.fires.filter(([kind]) => kind === "signals.overlay").length).toBeGreaterThan(5);
  });

  it("names its series' peers only among the daily series it draws", () => {
    const daily = new Set(out.nodes.filter((n) => n.k === "series" && n.id.startsWith("d:")).map((n) => n.id.slice(2)));
    for (const n of out.nodes) {
      for (const [peer, corr] of (n.m.peers ?? []) as [string, number][]) {
        expect(daily.has(peer), `${n.id} peer ${peer}`).toBe(true);
        expect(Math.abs(corr)).toBeLessThanOrEqual(1);
      }
    }
  });
});

describe("FIXTURE", () => {
  it("uses every edge kind", () => {
    expect(new Set(FIXTURE.edges.map((e) => e[2]))).toEqual(new Set(EDGE_KINDS));
  });

  it("has the nodes the Machine's tests lean on", () => {
    const ids = new Set(FIXTURE.nodes.map((n) => n.id));
    for (const id of [
      "feed:prices", "d:SPY", "d:NVDA", "d:AAPL", "s:NVDA", "m:NVDA", "o:NVDA", "f:VIXCLS",
      "j:ingest.bars_daily", "j:vol.forecast_league", "j:farm.sweep", "j:signals.daily", "j:signals.overlay", "j:desk.report", "j:regime.hmm",
      "p:vol.forecast_league", "p:farm.sweep", "p:signals.daily", "p:signals.overlay", "p:desk.report", "p:regime.hmm", "p:research",
      "book", "lim:aapl-cap", "lim:dd-cap", "kill",
    ]) expect(ids.has(id), id).toBe(true);
    expect(FIXTURE.nodes.filter((n) => n.k === "result")).toHaveLength(2);
    expect(FIXTURE.nodes.filter((n) => n.k === "strategy")).toHaveLength(2);
    expect(FIXTURE.notes).toEqual([]);
  });

  it("times the jobs at their New York hours", () => {
    const hour = (kind: string): number | undefined => byId(FIXTURE, `j:${kind}`).at.h;
    expect(hour("ingest.bars_daily")).toBe(18.5);
    expect(hour("vol.forecast_league")).toBe(21.5);
    expect(hour("farm.sweep")).toBe(23);
    expect(hour("signals.daily")).toBe(18.75);
    expect(hour("desk.report")).toBe(16.5);
  });

  it("carries the real overlay note and the real daily-signals note", () => {
    const [overlay] = byId(FIXTURE, "p:signals.overlay").m.notes as string[];
    expect(overlay.startsWith("exposure_scale 1.000000 = min(1, 0.1 / 0.033877); covariance lw_constant_corr on 504 sessions, ")).toBe(true);
    const [daily] = byId(FIXTURE, "p:signals.daily").m.notes as string[];
    expect(daily.startsWith("0 written, 0 written late, 1 written again (the desk did not size the last), 17 held, 0 already present, 3 refused; ")).toBe(true);
  });

  it("gives a ticker a short history: n below a year, and no vol", () => {
    const short = FIXTURE.nodes.filter((n) => n.k === "series" && typeof n.m.n === "number" && n.m.n < 253);
    expect(short.length).toBeGreaterThan(0);
    for (const n of short) {
      expect(n.m, n.id).not.toHaveProperty("vol");
      expect(n.m.spark, n.id).toHaveLength(n.m.n as number);
    }
    for (const id of ["d:SPY", "d:NVDA"]) expect(byId(FIXTURE, id).m).toMatchObject({ n: 253 });
  });

  it("keeps a readout's figures in step: the spark starts at 1 and ends at 1 + ret1y", () => {
    for (const n of FIXTURE.nodes.filter((x) => Array.isArray(x.m.spark))) {
      const spark = n.m.spark as number[];
      expect(spark[0], n.id).toBe(1);
      expect(spark.length, n.id).toBeLessThanOrEqual(61);
      expect(spark[spark.length - 1] - 1, n.id).toBeCloseTo(n.m.ret1y as number, 4);
    }
  });

  it("is the book it says: counts, capital and the limits the summary read", () => {
    const book = byId(FIXTURE, "book").m;
    expect(book.strategies).toBe(kindOf(FIXTURE, "strategy").length);
    expect(book.limits).toBe(kindOf(FIXTURE, "limit").length);
    const capital = kindOf(FIXTURE, "strategy").reduce((s, n) => s + (n.m.capital as number), 0);
    expect(book.capital).toBeCloseTo(capital, 6);
    expect(book).toMatchObject({ kill: "armed", positions: 14 });
    expect(book).not.toHaveProperty("summary_why");
    const kill = byId(FIXTURE, "kill");
    expect(kill.m).toEqual({ state: "armed", trips_on: ["dd-cap"] });
    expect(kill.at).toEqual({ i: kindOf(FIXTURE, "limit").length, n: kindOf(FIXTURE, "limit").length + 1 });
    expect(byId(FIXTURE, "lim:aapl-cap").m).toMatchObject({ scope: "instrument:AAPL", trips: false });
    expect(byId(FIXTURE, "lim:dd-cap").m).toMatchObject({ trips: true });
  });

  it("keeps the farm's lens in step with its cells", () => {
    const lens = byId(FIXTURE, "p:farm.sweep").m.lens as { cells: number; trials: number; board: unknown[][] };
    expect(lens.cells).toBe(lens.board.length);
    expect(lens.cells).toBe(kindOf(FIXTURE, "result").length);
    expect(lens.board.every((row) => row.length === 7)).toBe(true);
    const combos = kindOf(FIXTURE, "result").reduce((s, n) => s + (n.m.n_combos as number), 0);
    expect(lens.trials).toBe(combos);
  });
});

describe("the m fields are the server's own (machine/graph.py, lenses.py, readouts.py)", () => {
  const READOUT = ["spark", "ret1y", "vol", "last", "n", "peers"];

  it("series: a track, and the readout keys the server may send", () => {
    for (const n of kindOf(FIXTURE, "series")) {
      expect(n.m.track, n.id).toBeDefined();
      const extra = keys(n).filter((k) => !["track", "unis", ...READOUT].includes(k));
      expect(extra, n.id).toEqual([]);
    }
    expect(byId(FIXTURE, "d:SPY").m.unis).toEqual(["US equity indices"]);
  });

  it("feed, job, strategy, limit, kill and the research product", () => {
    expect(keys(byId(FIXTURE, "feed:prices"))).toEqual(["asof", "behind", "dataset", "keys", "ok", "who"]);
    for (const j of kindOf(FIXTURE, "job")) expect(keys(j), j.id).toEqual(["cron", "fires", "heavy", "kind", "mem", "when"]);
    for (const s of kindOf(FIXTURE, "strategy")) expect(keys(s), s.id).toEqual(["capital", "producer", "strategy", "symbols", "universe"]);
    for (const l of kindOf(FIXTURE, "limit")) expect(keys(l), l.id).toEqual(["kind", "scope", "trips"]);
    expect(keys(byId(FIXTURE, "kill"))).toEqual(["state", "trips_on"]);
    expect(keys(byId(FIXTURE, "p:research"))).toEqual(["producer", "strategies"]);
  });

  it("products: the manifest's fields, then a lens or why there is none; desk.report only its run times", () => {
    const manifest = ["art", "asof", "code", "cpu", "detail", "engine", "finished", "kind", "notes", "stale", "verdict"];
    expect(keys(byId(FIXTURE, "p:desk.report"))).toEqual(["finished", "kind", "stale"]);
    for (const n of kindOf(FIXTURE, "product").filter((p) => p.id !== "p:research" && p.id !== "p:desk.report")) {
      expect(keys(n).filter((k) => k !== "lens"), n.id).toEqual(manifest);
    }
    for (const kind of ["vol.forecast_league", "farm.sweep", "regime.hmm"]) expect(byId(FIXTURE, `p:${kind}`).m, kind).toHaveProperty("lens");
    for (const kind of ["signals.daily", "signals.overlay"]) {
      expect(byId(FIXTURE, `p:${kind}`).m, kind).not.toHaveProperty("lens");
      expect(byId(FIXTURE, `p:${kind}`).m, kind).not.toHaveProperty("lens_why");
    }
  });

  it("lens payloads", () => {
    expect(Object.keys(byId(FIXTURE, "p:farm.sweep").m.lens as object).sort()).toEqual(["board", "cells", "trials"]);
    expect(Object.keys(byId(FIXTURE, "p:vol.forecast_league").m.lens as object)).toEqual(["models"]);
    expect(Object.keys(byId(FIXTURE, "p:regime.hmm").m.lens as object).sort()).toEqual(["date", "p_high", "weeks"]);
  });

  it("farm cells: the cells table's fields and the leaderboard's", () => {
    for (const n of kindOf(FIXTURE, "result")) {
      expect(keys(n), n.id).toEqual(["combos", "detail", "dsr_farm", "n_combos", "oos_sharpe_ann", "pbo", "psr", "reason", "status", "verdict"]);
    }
  });

  it("the book: its configuration, then the summary's fields or why there is none", () => {
    expect(keys(byId(FIXTURE, "book"))).toEqual(["canary_cap", "capital", "incubation_cap", "kill", "limits", "positions", "strategies", "summary_asof", "vol_target"]);
  });
});

describe("FIXTURE_NO_SUMMARY", () => {
  it("has no limits and no kill switch, and the book says why", () => {
    expect(FIXTURE_NO_SUMMARY.nodes.filter((n) => n.k === "limit" || n.k === "kill")).toEqual([]);
    const edgeKinds = new Set(FIXTURE_NO_SUMMARY.edges.map((e) => e[2]));
    for (const k of ["limit", "trip", "halt"] as const) expect(edgeKinds.has(k), k).toBe(false);
    const book = byId(FIXTURE_NO_SUMMARY, "book").m;
    expect(keys(byId(FIXTURE_NO_SUMMARY, "book"))).toEqual(["canary_cap", "capital", "incubation_cap", "strategies", "summary_why", "vol_target"]);
    expect(typeof book.summary_why).toBe("string");
    expect(FIXTURE_NO_SUMMARY.notes).toEqual([`${book.summary_why as string}.`]);
  });

  it("is otherwise the fixture", () => {
    const keep = (out: MachineOut) => out.nodes.filter((n) => !["book", "lim:aapl-cap", "lim:dd-cap", "kill"].includes(n.id));
    expect(keep(FIXTURE_NO_SUMMARY)).toEqual(keep(FIXTURE));
    expect(FIXTURE_NO_SUMMARY.runs).toEqual(FIXTURE.runs);
  });
});

describe("FIXTURE_BROKEN", () => {
  it("has the farm product with no lens and the reason it could not be read", () => {
    const m = byId(FIXTURE_BROKEN, "p:farm.sweep").m;
    expect(m).not.toHaveProperty("lens");
    expect(typeof m.lens_why).toBe("string");
    expect(m.lens_why).not.toBe("");
  });

  it("is otherwise the fixture", () => {
    const rest = (out: MachineOut) => out.nodes.filter((n) => n.id !== "p:farm.sweep");
    expect(rest(FIXTURE_BROKEN)).toEqual(rest(FIXTURE));
    expect(FIXTURE_BROKEN.edges).toEqual(FIXTURE.edges);
    expect(FIXTURE_BROKEN.runs).toEqual(FIXTURE.runs);
  });
});

// Every test in the Machine shares these, so none may change them (controller ruling 19). A test that needs
// to change one clones it first: structuredClone(FIXTURE).
const isDeepFrozen = (v: unknown): boolean =>
  typeof v !== "object" || v === null || (Object.isFrozen(v) && Object.values(v).every(isDeepFrozen));

describe.each(VARIANTS)("%s cannot be changed by a test", (_name, out) => {
  it("is frozen all the way down, so a mutation throws", () => {
    expect(isDeepFrozen(out)).toBe(true);
    expect(() => { out.nodes[0].m.last = 1; }).toThrow(TypeError);
    expect(() => { out.nodes[0].at.i = 9; }).toThrow(TypeError);
    expect(() => { out.runs[0].state = "failed"; }).toThrow(TypeError);
    expect(() => out.runs.push(out.runs[0])).toThrow(TypeError);
    expect(() => out.edges.pop()).toThrow(TypeError);
    expect(() => out.x.fires.sort()).toThrow(TypeError);
  });

  it("can be cloned into something a test may change", () => {
    const copy = structuredClone(out);
    copy.runs[0].state = "failed";
    copy.nodes[0].m.last = 1;
    expect(copy).not.toEqual(out);
    expect(out.runs[0].state).toBe("done");
  });
});

describe("the variants share no objects", () => {
  it("has its own node, run and edge objects in each", () => {
    for (const [a, b] of [[FIXTURE, FIXTURE_NO_SUMMARY], [FIXTURE, FIXTURE_BROKEN], [FIXTURE_NO_SUMMARY, FIXTURE_BROKEN]]) {
      expect(a.nodes).not.toBe(b.nodes);
      expect(a.runs).not.toBe(b.runs);
      expect(a.edges).not.toBe(b.edges);
      for (const n of a.nodes) {
        const twin = b.nodes.find((x) => x.id === n.id);
        if (twin) {
          expect(twin, n.id).not.toBe(n);
          expect(twin.m, n.id).not.toBe(n.m);
        }
      }
      a.runs.forEach((r, i) => expect(b.runs[i]).not.toBe(r));
    }
  });
});

describe("FIXTURE's runs", () => {
  const runs = FIXTURE.runs;

  it("are oldest first, as load_runs sends them, the queued run (no start) last", () => {
    const started = runs.filter((r) => r.start !== null).map((r) => r.start as string);
    expect([...started].sort()).toEqual(started);
    expect(runs[runs.length - 1].state).toBe("queued");
  });

  it("include a failed run: it ended, it cost cpu, and its msg is the last progress text, never error text", () => {
    const failed = runs.filter((r) => r.state === "failed");
    expect(failed.length).toBeGreaterThan(0);
    for (const r of failed) {
      expect(r.end).not.toBeNull();
      expect(r.cpu).not.toBeNull();
      expect(r.progress).toBeGreaterThan(0);
      expect(r.progress).toBeLessThan(1);
      expect(typeof r.msg).toBe("string");
      expect(r.msg).not.toMatch(/error|exception|traceback|retrying|\/|\\|failed/i);
    }
  });

  it("include a done run with the handler's last progress message", () => {
    const done = runs.filter((r) => r.state === "done" && r.msg !== null);
    expect(done.length).toBeGreaterThan(0);
    expect(done.every((r) => r.progress === 1)).toBe(true);
  });

  it("carry error text in none of them", () => {
    expect(JSON.stringify(runs)).not.toMatch(/retrying after|Traceback|Error:/);
  });
});
