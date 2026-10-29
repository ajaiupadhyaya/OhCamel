/**
 * A small graph in the exact shape `GET /api/machine` sends, written by hand for the Machine's tests (layout,
 * webs, readings, the tour, search). It is a miniature of the real thing, and its `m` fields are the keys
 * machine/graph.py, lenses.py and readouts.py really build, with values of the kind they really hold. A test
 * (fixture.test.ts) holds it to spec section 5's rules, so it cannot drift: every edge obeys its kind's rule
 * and all twelve kinds appear.
 *
 * Read it as of 2026-10-28 18:51 in New York. The 18:45 daily signals run is under way, the 18:50 overlay
 * run is queued behind it, and the rest of the day's runs are done. AAPL's readout rests on two closes (a
 * short history: no `vol`, and `ret1y` is not a year's return); NVDA's rests on 253 (a year).
 *
 * `FIXTURE_NO_SUMMARY` is the book when desk.report's summary cannot be shown: no limits, no kill switch,
 * `book.m.summary_why` says why. `FIXTURE_BROKEN` is the farm product whose lens could not be read.
 */
import type { Edge, EdgeKind, MachineOut, MNode, NodeKind, Ring, Run } from "./types";

const node = (
  id: string, k: NodeKind, l: string, n: string, ring: Ring, at: MNode["at"], m: MNode["m"],
): MNode => ({ id, k, l, n, ring, at, m });

const SHA = "0dfae1f";
const SUMMARY_WHY = "book_summary has no row: the engine was unreachable when desk.report last ran";

// ------------------------------------------------------------------ the world: series
const SPARK_SPY = [
  1, 0.9918, 1.0149, 1.0189, 0.9854, 0.9907, 1.0188, 1.0107, 1.0087, 0.9974, 0.9832, 0.9887, 1.0032, 0.9831, 0.9648, 0.9505, 0.9551,
  0.9838, 0.9997, 0.971, 0.9865, 1.001, 1.0086, 1.005, 1.0073, 1.0272, 1.0372, 1.0728, 1.0784, 1.0645, 1.076, 1.0955, 1.0655, 1.0812,
  1.0806, 1.0712, 1.0723, 1.1048, 1.1245, 1.128, 1.0903, 1.1152, 1.1345, 1.1378, 1.1462, 1.1781, 1.2038, 1.1727, 1.1903, 1.2261, 1.1986, 1.1873,
];
const SPARK_NVDA = [
  1, 0.9497, 0.9187, 1.0035, 0.9989, 0.9949, 1.0122, 0.9082, 0.9762, 0.9891, 0.9853, 1.0328, 1.0568, 1.138, 1.1479, 1.1915, 1.2977,
  1.3938, 1.5767, 1.5685, 1.7141, 1.6607, 1.6671, 1.6096, 1.6949, 1.906, 2.0412, 1.9132, 1.93, 1.9473, 2.0007, 2.032, 1.946, 2.0143,
  1.8274, 1.835, 1.73, 1.6294, 1.6857, 1.6272, 1.6945, 1.4876, 1.5523, 1.4815, 1.5244, 1.4991, 1.3851, 1.3834, 1.3867, 1.4334, 1.3942, 1.4127,
];

const WORLD: MNode[] = [
  node("d:SPY", "series", "SPY", "SPDR S&P 500 ETF Trust", "daily", { sec: "etf", i: 0 }, {
    track: "daily", unis: ["US equity indices"],
    spark: SPARK_SPY, ret1y: 0.1873, vol: 0.1325, last: 688.63, n: 253, peers: [["NVDA", 0.78]],
  }),
  node("d:NVDA", "series", "NVDA", "Nvidia", "daily", { sec: "eq", i: 0 }, {
    track: "daily", spark: SPARK_NVDA, ret1y: 0.4127, vol: 0.4393, last: 172.35, n: 253, peers: [["SPY", 0.78]],
  }),
  node("s:NVDA", "series", "NVDA", "Nvidia: SEC filings", "filings", { of: "d:NVDA" }, { track: "filings" }),
  // two closes: a short history (vol needs two returns, so there is none, and ret1y is the return over two sessions)
  node("d:AAPL", "series", "AAPL", "Apple Inc.", "daily", { sec: "eq", i: 1 }, {
    track: "daily", spark: [1, 0.9879], ret1y: -0.0121, last: 261.37, n: 2, peers: [],
  }),
  node("f:VIXCLS", "series", "VIXCLS", "VIX", "daily", { sec: "fred", i: 0 }, { track: "macro" }),
  node("m:NVDA", "series", "NVDA", "NVDA: one-minute bars", "minute", { of: "d:NVDA" }, { track: "minute" }),
  node("o:NVDA", "series", "NVDA", "NVDA: option chain", "options", { of: "d:NVDA" }, { track: "options" }),
];

// ------------------------------------------------------------------ the clock: every job at its New York hour
const OVERLAY_FIRES = [
  9.3333, 9.6667, 10, 10.25, 10.5, 10.75, 11, 11.25, 11.5, 11.75, 12, 12.25, 12.5, 12.75, 13, 13.25, 13.5, 13.75, 14, 14.25, 14.5, 14.75,
  15, 15.25, 15.5, 15.75, 18.6667, 18.8333, 20.1667,
];
const job = (kind: string, name: string, h: number, m: { fires: number[]; cron: string[]; heavy: boolean; mem: string; when: string[] }): MNode =>
  node(`j:${kind}`, "job", name, name, "clock", { h }, { kind, ...m });

const CLOCK: MNode[] = [
  job("ingest.bars_daily", "Daily bars", 18.5, { fires: [18.5], cron: ["30 18 * * 1-5"], heavy: false, mem: "S", when: ["weekdays"] }),
  job("vol.forecast_league", "Vol forecast league", 21.5, { fires: [21.5], cron: ["30 21 * * 1-5"], heavy: true, mem: "M", when: ["weekdays"] }),
  job("farm.sweep", "Strategy farm", 23, { fires: [23], cron: ["0 23 * * 1-5"], heavy: true, mem: "L", when: ["weekdays"] }),
  job("regime.hmm", "Regime model", 9, { fires: [9], cron: ["0 9 * * 6"], heavy: true, mem: "M", when: ["weekly"] }),
  job("signals.daily", "Daily signals", 18.75, { fires: [18.75], cron: ["45 18 * * 1-5"], heavy: false, mem: "S", when: ["weekdays"] }),
  // six schedule entries merge into one job, placed at the middle of its fires (they are under 12 hours apart)
  job("signals.overlay", "Exposure overlay", 14.75, {
    fires: OVERLAY_FIRES,
    cron: ["20 9 * * 1-5", "10 20 * * 1-5", "40 18 * * 1-5", "40 9 * * 1-5", "*/15 10-15 * * 1-5", "50 18 * * 1-5"],
    heavy: false, mem: "S", when: ["weekdays"],
  }),
  job("desk.report", "Desk report", 16.5, { fires: [16.5], cron: ["30 16 * * 1-5"], heavy: false, mem: "S", when: ["weekdays"] }),
];

// ------------------------------------------------------------------ the feed
const FEEDS: MNode[] = [
  node("feed:prices", "feed", "Prices", "Prices from Yahoo, Alpaca, Stooq", "feeds", {}, {
    who: "Yahoo, Alpaca, Stooq", dataset: "bars_daily", keys: 586, asof: "2026-10-28", ok: "2026-10-28T22:33:38.771204", behind: 2,
  }),
];

// ------------------------------------------------------------------ products: what each job published
const OVERLAY_NOTE =
  "exposure_scale 1.000000 = min(1, 0.1 / 0.033877); covariance lw_constant_corr on 504 sessions, diagonal from P2's best model by QLIKE " +
  "(EWMA(0.94) where P2 has none); regime p_high and core MC VaR are reported, never used";
const DAILY_NOTE =
  "0 written, 0 written late, 1 written again (the desk did not size the last), 17 held, 0 already present, 3 refused; parameters frozen " +
  "for the month (INC-01); documents only on each strategy's own decision sessions; validation from the newest farm verdict when it tested " +
  "the frozen parameters";
const EXECUTION_NOTES = [
  "a farm verdict was earned at the strategy's default_max_leverage (2-3x gross for tsmom, risk_parity and low_beta), while the desk trades " +
    "these de-levered 1x targets: a cell's verdict is not evidence at the leverage traded",
  "the backtest engine fills at the close of d+1 (execution_lag=1); the desk fills at the open of d+1 by market-on-open: the farm's fill " +
    "price is not the desk's",
];

const product = (kind: string, code: string, name: string, m: Record<string, unknown>): MNode =>
  node(`p:${kind}`, "product", code, name, "products", { of: `j:${kind}` }, { kind, ...m });

// a manifest's fields; `stale` is the artifact's own flag, false for all of these
const made = (art: string, finished: string, cpu: number, asof: string, engine: string) =>
  ({ code: SHA, asof, finished, cpu, engine, notes: [] as string[], art, stale: false });

const FARM_LENS = {
  cells: 2, trials: 48,
  board: [
    ["tsmom@us_equity_indices", "PASS", 0.8123, 0.9712, 0.9884, 0.2143, 24],
    ["xsmom@sectors", "FAIL", 0.2217, 0.3871, 0.7142, 0.5612, 24],
  ],
};
const FARM_LENS_WHY = "leaderboard has no column oos_sharpe_ann";

const products = (lens: boolean): MNode[] => [
  product("vol.forecast_league", "P2", "Vol forecast league", {
    verdict: "DESCRIPTIVE ONLY", detail: "FORECAST LEAGUE · HAR IN 10% MCS · 61% OF 49 NAMES",
    ...made("01M6021RA4P60KNJAPA96SPAVR", "2026-10-28T01:48:11.904Z", 1043.9, "2026-10-27", "rust"),
    notes: ["out of sample: last 250 sessions, refit every 21, window 1000; target is minute RV where stored, else the squared daily return (QLIKE is robust to it)"],
    lens: { models: [["har", 0.6122, 1.84, 49], ["garch", 0.5306, 2.31, 49], ["ewma", 0.3469, 3.02, 49]] },
  }),
  product("farm.sweep", "P4", "Strategy farm", {
    verdict: "PASS", detail: "1 PASS · 1 FAIL · 0 SKIPPED · ADVISORY",
    ...made("01M6076HR4Y1Y3G4CZQVWJEXHY", "2026-10-28T04:31:27.440Z", 5231.8, "2026-10-27", "python"),
    notes: ["2 cells run tonight; DSR deflated by all 48 trials in the farm; walk-forward 1260/252 sessions; costs from the engine's default (5.0 bps)"],
    ...(lens ? { lens: FARM_LENS } : { lens_why: FARM_LENS_WHY }),
  }),
  product("regime.hmm", "P6", "Regime model", {
    verdict: "INSUFFICIENT DATA", detail: "STALE HOLDOUT · METHODOLOGY V1 · OWNER DECISION",
    ...made("01M5PZYA04FVYK8V7GJ2HMCBC9", "2026-10-24T13:07:33.610Z", 212.6, "2026-10-23", "python"),
    notes: ["filtered probabilities are scored; p_high_smoothed_history uses later data and is display-only history"],
    lens: { date: "2026-10-23", p_high: 0.1832, weeks: 436 },
  }),
  // the 18:45 run is still going, so the newest signals.daily artifact is Tuesday's
  product("signals.daily", "SD", "Daily signals", {
    verdict: null, detail: null,
    ...made("01M5ZRKMB485RHRC5KDBCMC7C5", "2026-10-27T22:46:02.774Z", 9.8, "2026-10-27", "python"),
    notes: [DAILY_NOTE, ...EXECUTION_NOTES],
  }),
  product("signals.overlay", "SO", "Exposure overlay", {
    verdict: null, detail: null,
    ...made("01M62AQ6C4CPX3DEJZDNW4GCA5", "2026-10-28T22:40:19.866Z", 17.3, "2026-10-28", "python"),
    notes: [OVERLAY_NOTE],
  }),
  // desk.report's node shows only its run times (owner, 2026-10-29)
  product("desk.report", "DR", "Desk report", { finished: "2026-10-28T20:30:44.907Z", stale: false }),
];

// ------------------------------------------------------------------ results inside the farm product
const cell = (s: string, u: string, i: number, m: Record<string, unknown>): MNode =>
  node(`c:${s}@${u}`, "result", `${s}@${u}`, `${s.replace(/_/g, " ")} on ${u.replace(/_/g, " ")}`, "results", { of: "p:farm.sweep", i, n: 2 }, m);

const RESULTS: MNode[] = [
  cell("tsmom", "us_equity_indices", 0, {
    status: "ok", reason: null, combos: 24, verdict: "PASS", detail: "ALL GATES PASS",
    dsr_farm: 0.9712, psr: 0.9884, oos_sharpe_ann: 0.8123, pbo: 0.2143, n_combos: 24,
  }),
  cell("xsmom", "sectors", 1, {
    status: "ok", reason: null, combos: 24, verdict: "FAIL", detail: "DEFLATED SHARPE BELOW 0.95",
    dsr_farm: 0.3871, psr: 0.7142, oos_sharpe_ann: 0.2217, pbo: 0.5612, n_combos: 24,
  }),
];

// ------------------------------------------------------------------ the desk: strategies, the book, its limits and kill switch
const BOOK_CONFIG = { strategies: 2, capital: 0.05, incubation_cap: 0.6, canary_cap: 0.1, vol_target: 0.1 };

const desk = (summary: boolean): MNode[] => [
  node("p:research", "product", "RS", "Research service", "products", { beside: "p:signals.daily" }, { producer: "research-service", strategies: 1 }),
  node("book", "book", "THE BOOK", "The paper book at Alpaca", "book", {}, summary
    ? { ...BOOK_CONFIG, positions: 14, kill: "armed", limits: 2, summary_asof: "2026-10-28T20:30:12Z" }
    : { ...BOOK_CONFIG, summary_why: SUMMARY_WHY }),
  node("st:exp_a01_spy", "strategy", "exp a01 spy", "exp_a01_spy", "strategies", { i: 0, n: 2 }, {
    producer: "research-service", strategy: null, universe: null, symbols: [], capital: 0.025,
  }),
  node("st:farm_tsmom_us_equity_indices", "strategy", "tsmom us equity indices", "farm_tsmom_us_equity_indices", "strategies", { i: 1, n: 2 }, {
    producer: "signals.daily", strategy: "tsmom", universe: "us_equity_indices", symbols: ["SPY", "QQQ", "DIA", "IWM", "MDY"], capital: 0.025,
  }),
  // with a summary, the limits (name order) and then the kill switch in the last slot
  ...(summary ? [
    node("lim:aapl-cap", "limit", "aapl-cap", "aapl-cap: gross_notional, instrument:AAPL", "limits", { i: 0, n: 3 },
      { kind: "gross_notional", scope: "instrument:AAPL", trips: false }),
    node("lim:dd-cap", "limit", "dd-cap", "dd-cap: max_drawdown, portfolio", "limits", { i: 1, n: 3 },
      { kind: "max_drawdown", scope: "portfolio", trips: true }),
    node("kill", "kill", "KILL SWITCH", "Kill switch: armed", "limits", { i: 2, n: 3 }, { state: "armed", trips_on: ["dd-cap"] }),
  ] : []),
];

// ------------------------------------------------------------------ edges, by id; the build drops one whose end is absent
const LINKS: [string, string, EdgeKind][] = [
  ["feed:prices", "j:ingest.bars_daily", "pull"],
  ["j:ingest.bars_daily", "d:SPY", "write"], ["j:ingest.bars_daily", "d:NVDA", "write"], ["j:ingest.bars_daily", "d:AAPL", "write"],
  ["j:vol.forecast_league", "p:vol.forecast_league", "publish"], ["j:farm.sweep", "p:farm.sweep", "publish"],
  ["j:regime.hmm", "p:regime.hmm", "publish"], ["j:signals.daily", "p:signals.daily", "publish"],
  ["j:signals.overlay", "p:signals.overlay", "publish"], ["j:desk.report", "p:desk.report", "publish"],
  ["d:SPY", "j:vol.forecast_league", "read"], ["d:NVDA", "j:vol.forecast_league", "read"], ["m:NVDA", "j:vol.forecast_league", "read"],
  ["d:SPY", "j:farm.sweep", "read"],
  ["d:SPY", "j:regime.hmm", "read"], ["f:VIXCLS", "j:regime.hmm", "read"],
  ["p:farm.sweep", "j:signals.daily", "read"],
  ["p:vol.forecast_league", "j:signals.overlay", "read"],
  ["p:farm.sweep", "c:tsmom@us_equity_indices", "result"], ["p:farm.sweep", "c:xsmom@sectors", "result"],
  ["p:research", "st:exp_a01_spy", "signal"], ["st:exp_a01_spy", "book", "trade"],
  ["c:tsmom@us_equity_indices", "st:farm_tsmom_us_equity_indices", "roster"], ["p:signals.daily", "st:farm_tsmom_us_equity_indices", "signal"],
  ["st:farm_tsmom_us_equity_indices", "book", "trade"],
  ["p:signals.overlay", "book", "scale"], ["book", "j:desk.report", "read"],
  ["lim:aapl-cap", "book", "limit"], ["d:AAPL", "lim:aapl-cap", "read"], ["lim:dd-cap", "book", "limit"],
  ["lim:dd-cap", "kill", "trip"], ["kill", "book", "halt"],
];

// ------------------------------------------------------------------ the day's runs, oldest first
const run = (id: string, kind: string, state: Run["state"], start: string | null, end: string | null, cpu: number | null,
  progress: number | null, msg: string | null): Run => ({ id, kind, state, start, end, cpu, progress, msg });

const RUNS: Run[] = [
  run("01M6021RA4P60KNJAPA96SPAVR", "vol.forecast_league", "done", "2026-10-28T01:30:02.115Z", "2026-10-28T01:48:11.904Z", 1043.9, 1, null),
  run("01M6076HR4Y1Y3G4CZQVWJEXHY", "farm.sweep", "done", "2026-10-28T03:00:01.804Z", "2026-10-28T04:31:27.440Z", 5231.8, 1, null),
  run("01M62395641CESN081A6974NQ3", "desk.report", "done", "2026-10-28T20:30:01.118Z", "2026-10-28T20:30:44.907Z", 6.2, 1, null),
  run("01M62A4WE49XXBSPV775XG3DNE", "ingest.bars_daily", "done", "2026-10-28T22:30:01.402Z", "2026-10-28T22:33:40.214Z", 128.4, 1, null),
  run("01M62AQ6C4CPX3DEJZDNW4GCA5", "signals.overlay", "done", "2026-10-28T22:40:01.350Z", "2026-10-28T22:40:19.866Z", 17.3, 1, null),
  run("01M62B0BB49PABRJV2BRWE853B", "signals.daily", "running", "2026-10-28T22:45:01.221Z", null, null, 0.7, "farm_tsmom_credit"),
  run("01M62B9GA4XD47RXN7Z58E12E6", "signals.overlay", "queued", null, null, null, null, null),
];

// ------------------------------------------------------------------ assembly
const round3 = (h: number): number => Math.round(h * 1000) / 1000;

/** Every fire of every job as `[kind, hour]`, three places, sorted by kind and then hour: the server's `x.fires`. */
function firesOf(nodes: MNode[]): [string, number][] {
  return nodes
    .filter((n) => n.k === "job")
    .flatMap((n) => (n.m.fires as number[]).map((h): [string, number] => [n.m.kind as string, round3(h)]))
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] - b[1]));
}

function build(opts: { summary: boolean; lens: boolean }): MachineOut {
  const nodes = [...WORLD, ...CLOCK, ...FEEDS, ...products(opts.lens), ...RESULTS, ...desk(opts.summary)];
  const at = new Map(nodes.map((n, i) => [n.id, i]));
  const edges = LINKS.flatMap(([a, b, kind]): Edge[] => {
    const from = at.get(a);
    const to = at.get(b);
    return from === undefined || to === undefined ? [] : [[from, to, kind]];
  });
  return {
    asof: "2026-10-28T22:51:12.388Z",
    nodes,
    edges,
    runs: RUNS,
    x: { fires: firesOf(nodes) },
    provenance: [{ source: "ohcamel-machine", fetched_at: "2026-10-28T22:51:12.388114+00:00", synthetic: false, detail: { store: "jobs.sqlite+warehouse" } }],
    notes: opts.summary ? [] : [`${SUMMARY_WHY}.`],
  };
}

export const FIXTURE: MachineOut = build({ summary: true, lens: true });
export const FIXTURE_NO_SUMMARY: MachineOut = build({ summary: false, lens: true });
export const FIXTURE_BROKEN: MachineOut = build({ summary: true, lens: false });
