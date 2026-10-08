import { describe, expect, test } from "vitest";
import type { History, Snapshot } from "../engine/types";
import { appendTrail, fromEngine, fromReading, historyToTape, parseTs, readAge, tapeFromServer, trailToTape, type DeckModel, type Reading } from "./model";
import { layout } from "./scope";
import { radar } from "./radar";

// A reading as the contract describes it: the default book, one limit breached, one that
// cannot be evaluated, one mark failed.
function reading(over: Partial<Reading> = {}): Reading {
  return {
    as_of: "2026-09-24T14:31:00Z",
    source: "portfolio",
    clock: { is_open: true, next_open: "2026-09-25T13:30:00Z", next_close: "2026-09-24T20:00:00Z", source: "alpaca", note: null },
    book: { notional: 1_000_000, equity_usd: 1_004_000, day_pnl: 0.004, day_pnl_usd: 4000, gross: 1.0, net: 1.0, cash: 0, observations: 2500, start: "2016-09-26", end: "2026-09-23" },
    risk: { alpha: 0.95, var: 0.012, es: 0.015, var_usd: 12000, es_usd: 15000, hist_var: 0.013, hist_es: 0.019, model: "ewma", ewma_lambda: 0.94 },
    marks: [
      { ticker: "SPY", price: 571.2, prev_close: 568, change_pct: 0.00563, as_of: "2026-09-24T14:30:10Z", age_s: 50, source: "alpaca-sip", weight: 0.4, live_weight: 0.4012, error: null },
      { ticker: "TLT", price: 92.1, prev_close: 92.3, change_pct: -0.00217, as_of: "2026-09-24T14:29:00Z", age_s: 120, source: "alpaca-sip", weight: 0.15, live_weight: 0.1492, error: null },
      { ticker: "GLD", price: null, prev_close: 230, change_pct: null, as_of: null, age_s: 9999, source: null, weight: 0.1, live_weight: 0.1, error: "no quote" },
    ],
    blips: [
      { ticker: "SPY", live_weight: 0.4012, change_pct: 0.00563, component_var: 0.0095, component_var_usd: 9500, pct_var: 0.79 },
      { ticker: "TLT", live_weight: 0.1492, change_pct: -0.00217, component_var: -0.0004, component_var_usd: -400, pct_var: -0.033 },
    ],
    limits: [
      { name: "gross-cap", kind: "gross", scope: "book", unit: "fraction", observed: 1.0, threshold: 1.5, excess: -0.5, breached: false, utilisation: 0.6667 },
      { name: "var-cap", kind: "var", scope: "book", unit: "USD", observed: 12000, threshold: 20000, excess: -8000, breached: false, utilisation: 0.6 },
      { name: "name-cap", kind: "name", scope: "SPY", unit: "fraction", observed: 0.4012, threshold: 0.35, excess: 0.0512, breached: true, utilisation: 1.1463 },
    ],
    unevaluated: [{ name: "dd-cap", kind: "drawdown", reason: "no history for GLD" }],
    feeds: [{ key: "quotes", label: "Quotes", state: "ok", detail: "alpaca SIP", age_s: 50 }],
    notes: ["GLD has no quote; it keeps its close weight."],
    provenance: [{ source: "alpaca", fetched_at: "2026-09-24T14:31:00Z" }],
    ...over,
  };
}

function snapshot(over: Partial<Snapshot> = {}): { snapshot: Snapshot; notes?: string[]; provenance?: [] } {
  return {
    snapshot: {
      as_of: "2026-09-24 14:31:00",
      positions: [
        { symbol: "AAPL", sector: "Tech", exposure: 200_000, weight: 0.2, component_var: 3000, price: 230, qty: 870, marginal: null, standalone: null, risk_share: 0.6, risk_over_money: 3 },
        { symbol: "TLT", sector: "Rates", exposure: -50_000, weight: -0.05, component_var: -200, price: 92, qty: -543, marginal: null, standalone: null, risk_share: -0.04, risk_over_money: null },
      ],
      gross_exposure: 250_000,
      equity: 400_000,
      value_at_risk_notional: 5000,
      expected_shortfall_notional: 6500,
      warming_up: false,
      nodes_recomputed: 12345,
      feed: {
        healthy: false,
        stale: ["TLT"],
        never_seen: ["MSFT"],
        symbols: [
          { symbol: "AAPL", last_tick: "2026-09-24T14:30:55Z", never_seen: false, stale: false },
          { symbol: "TLT", last_tick: "2026-09-24T14:20:00Z", never_seen: false, stale: true },
          { symbol: "MSFT", last_tick: null, never_seen: true, stale: false },
        ],
      },
      limits: [{ name: "book-cap", scope: "book", unit: "USD", observed: 250_000, threshold: 400_000, excess: -150_000, breached: false, utilisation: 0.625 }],
      unevaluated: ["var-cap"],
      ...over,
    },
    notes: ["Engine mode: demo."],
  };
}
const NOW = Date.parse("2026-09-24T14:31:00Z");

describe("fromReading", () => {
  test("carries the contract's fields through unchanged", () => {
    const r = reading();
    const m = fromReading(r, "reference");
    expect(m.source).toBe("reference");
    expect(m.as_of).toBe(r.as_of);
    expect(m.clock).toEqual(r.clock);
    expect(m.notional).toBe(1_000_000);
    expect(m.alpha).toBe(0.95);
    expect(m.limits).toBe(r.limits);
    expect(m.unevaluated).toEqual([{ name: "dd-cap", kind: "drawdown", reason: "no history for GLD" }]);
    expect(m.blips).toBe(r.blips);
    expect(m.feeds).toBe(r.feeds);
    expect(m.notes).toEqual(["GLD has no quote; it keeps its close weight."]);
    expect(m.provenance).toHaveLength(1);
  });

  test("counters: day P&L, VaR, ES, and the oldest age among marks that were served", () => {
    const m = fromReading(reading());
    expect(m.counters).toEqual({ day_pnl: 0.004, day_pnl_usd: 4000, var_usd: 12000, es_usd: 15000, marks_age_s: 120 });
  });

  test("tolerates missing lists and a string note", () => {
    const m = fromReading(reading({ marks: undefined as never, limits: undefined as never, notes: "one note" }));
    expect(m.marks).toEqual([]);
    expect(m.limits).toEqual([]);
    expect(m.counters.marks_age_s).toBe(null);
    expect(m.notes).toEqual(["one note"]);
  });

  test("feeds the targeting computer and the radar directly", () => {
    const m = fromReading(reading());
    const s = layout(m.limits, m.unevaluated.map((u) => u.name));
    expect(s.target).toBe("gross-cap");
    expect(s.rails.map((r) => r.name)).toEqual(["name-cap"]);
    expect(s.unknown).toEqual(["dd-cap"]);
    const rd = radar(m.blips);
    expect(rd.blips.find((b) => b.ticker === "TLT")!.hedge).toBe(true);
  });
});

describe("fromEngine", () => {
  test("limits pass through; unevaluated names gain a reason", () => {
    const m = fromEngine(snapshot(), NOW);
    expect(m.source).toBe("engine");
    expect(m.limits.map((l) => [l.name, l.utilisation, l.unit])).toEqual([["book-cap", 0.625, "USD"]]);
    expect(m.unevaluated).toEqual([{ name: "var-cap", kind: null, reason: "cannot be evaluated" }]);
  });

  test("positions become blips: risk_share as share of VaR, exposure / equity as weight", () => {
    const m = fromEngine(snapshot(), NOW);
    expect(m.blips).toEqual([
      { ticker: "AAPL", live_weight: 0.5, change_pct: null, component_var: 3000, component_var_usd: 3000, pct_var: 0.6 },
      { ticker: "TLT", live_weight: -0.125, change_pct: null, component_var: -200, component_var_usd: -200, pct_var: -0.04 },
    ]);
  });

  test("without equity the engine's own weight is used", () => {
    const m = fromEngine(snapshot({ equity: null }), NOW);
    expect(m.blips.map((b) => b.live_weight)).toEqual([0.2, -0.05]);
    expect(m.notional).toBe(null);
  });

  test("feed health becomes lamps: never seen is down, stale is stale; the bridge is a lamp", () => {
    const m = fromEngine(snapshot(), NOW);
    expect(m.feeds.map((f) => [f.label, f.state, f.age_s])).toEqual([
      ["Engine bridge", "ok", 0],
      ["AAPL", "ok", 5],
      ["TLT", "stale", 660],
      ["MSFT", "down", null],
    ]);
  });

  test("no clock, no day P&L; VaR/ES in dollars; marks age is the oldest tick seen", () => {
    const m = fromEngine(snapshot(), NOW);
    expect(m.clock).toBe(null);
    expect(m.counters).toEqual({ day_pnl: null, day_pnl_usd: null, var_usd: 5000, es_usd: 6500, marks_age_s: 660 });
    expect(m.notes[0]).toBe("Engine mode: demo.");
    expect(m.notes.at(-1)).toMatch(/no session clock/);
  });

  test("warming up dims the bridge lamp; nulls stay null", () => {
    const m = fromEngine(snapshot({ warming_up: true, value_at_risk_notional: null, limits: undefined, unevaluated: undefined, feed: undefined }), NOW);
    expect(m.feeds).toHaveLength(1);
    expect(m.feeds[0].state).toBe("stale");
    expect(m.counters.var_usd).toBe(null);
    expect(m.limits).toEqual([]);
    expect(m.unevaluated).toEqual([]);
  });

  test("marks carry the engine's price and tick age", () => {
    const m = fromEngine(snapshot(), NOW);
    expect(m.marks[0]).toMatchObject({ ticker: "AAPL", price: 230, age_s: 5, source: "ohcamel-engine", weight: 0.5, live_weight: 0.5 });
  });
});

describe("parseTs", () => {
  test("reads the engine's space-separated UTC stamps and ISO", () => {
    expect(parseTs("2026-09-24 14:31:00")).toBe(NOW);
    expect(parseTs("2026-09-24T14:31:00Z")).toBe(NOW);
    expect(parseTs("2026-09-24T10:31:00-04:00")).toBe(NOW);
    expect(parseTs("nonsense")).toBe(null);
    expect(parseTs(null)).toBe(null);
  });
});

describe("the session tape", () => {
  const m1 = fromReading(reading());
  const m2: DeckModel = { ...fromReading(reading({ as_of: "2026-09-24T14:31:15Z" })), counters: { ...m1.counters, day_pnl_usd: -2500 } };

  test("the trail adds each new reading once", () => {
    let t = appendTrail([], m1);
    t = appendTrail(t, m1);
    t = appendTrail(t, m2);
    expect(t.map((p) => p.ts)).toEqual([m1.as_of, m2.as_of]);
    expect(t[1].day_pnl_usd).toBe(-2500);
    expect(t[0].utilisation).toEqual({ "gross-cap": 0.6667, "var-cap": 0.6, "name-cap": 1.1463, "dd-cap": null });
  });

  test("the trail keeps the newest `max` points, in time order", () => {
    let t = appendTrail([], m2);
    t = appendTrail(t, m1, 1);
    expect(t.map((p) => p.ts)).toEqual([m2.as_of]);
  });

  test("a trail becomes columns; a limit that appears later is a gap before it", () => {
    let t = appendTrail([], m1);
    t = appendTrail(t, { ...m2, limits: [...m2.limits, { name: "es-cap", kind: "es", scope: "book", unit: "USD", observed: 1, threshold: 2, excess: -1, breached: false, utilisation: 0.5 }] });
    const c = trailToTape(t);
    expect(c.ts).toHaveLength(2);
    expect(c.utilisation["es-cap"]).toEqual([null, 0.5]);
    expect(c.day_pnl_usd).toEqual([4000, -2500]);
    expect(c.pnlLabel).toBe("Day P&L");
  });

  test("the server's tape passes through; an empty tape is empty, not an error", () => {
    const c = tapeFromServer({ n: 0, ts: [], day_pnl_usd: [], var_usd: [], es_usd: [], utilisation: {} });
    expect(c.ts).toEqual([]);
    expect(c.utilisation).toEqual({});
  });

  test("engine history: equity change since the first point, no utilisation", () => {
    const h: History = { points: 3, capacity: 100, appended: 3, time: [NOW, NOW + 1000, NOW + 2000], gross: [], net: [], equity: [null, 400_000, 399_000], drawdown: [], var_notional: [5000, 5100, null], es_notional: [6000, 6100, null] };
    const c = historyToTape(h);
    expect(c.ts[0]).toBe("2026-09-24T14:31:00.000Z");
    expect(c.day_pnl_usd).toEqual([null, 0, -1000]);
    expect(c.var_usd).toEqual([5000, 5100, null]);
    expect(c.utilisation).toEqual({});
    expect(c.pnlLabel).toMatch(/Equity change/);
  });
});


test("a cached quote response does not add an invented market observation", () => {
  const first = fromReading(reading());
  const trail = appendTrail([], first);
  const repeat = fromReading(reading({as_of: "2026-09-24T14:31:15Z"}));
  expect(appendTrail(trail, repeat)).toBe(trail);
  const changed = fromReading(reading({as_of: "2026-09-24T14:32:00Z", risk: {...reading().risk, var_usd: 12500}}));
  expect(appendTrail(trail, changed)).toHaveLength(2);
});

describe("readAge (the status strip's label)", () => {
  const now = new Date("2026-10-08T06:15:00Z");
  test("NOW inside 45 s", () => expect(readAge("2026-10-08 06:14:40+00:00", now)).toBe("READ NOW"));
  test("minutes in caps", () => expect(readAge("2026-10-08T06:12:00Z", now)).toBe("READ 3 MIN AGO"));
});
