import { describe, expect, it } from "vitest";
import { baseBody, initialConfig } from "./config";
import { tierHint } from "./Setup";
import type { Catalog, StrategySpec } from "./types";

const spec = { key: "buy_and_hold", params: [], default_tickers: ["SPY", "UNG"] } as unknown as StrategySpec;
const catalog = {
  engine_defaults: { cost_bps: null, tiers_bps: { "1": 1, "2": 3, "3": 8 }, ticker_tiers: { SPY: 1, UNG: 3 }, provisional_tiers: 42, borrow_bps: 50, financing_bps: 50, costs_file: "research/config/costs.yaml", costs_version: "1.0.0", execution_lag: 1, benchmark: "SPY", vol_estimator: "ewma", vol_halflife: 21, vol_window: 63 },
} as unknown as Catalog;

describe("Strategy Lab costs (completion task 2.3, fix round 1)", () => {
  it("sends no cost, borrow or financing unless the visitor typed one: the server reads the cost file", () => {
    const cfg = initialConfig(spec);
    expect([cfg.cost_bps, cfg.borrow_bps, cfg.financing_bps]).toEqual([null, null, null]);
    const body = baseBody(cfg, spec, cfg.tickers);
    expect([body.cost_bps, body.borrow_bps, body.financing_bps]).toEqual([null, null, null]);
    const mine = baseBody({ ...cfg, cost_bps: 7 }, spec, cfg.tickers);
    expect(mine.cost_bps).toBe(7);
  });

  it("says what the file charges each ticker, a ticker with no tier, and that the tiers are provisional", () => {
    expect(tierHint(catalog, ["SPY", "UNG", "AAPL"])).toBe("FILE · BP BY TIER · SPY 1 · UNG 8 · AAPL NO TIER · PROVISIONAL");
    const measured = { ...catalog, engine_defaults: { ...catalog.engine_defaults, provisional_tiers: 0 } } as Catalog;
    expect(tierHint(measured, ["SPY"])).toBe("FILE · BP BY TIER · SPY 1");
  });
});

describe("costBpsInfo (the cost field's tip)", () => {
  it("reads the tier half-spreads from the catalog, never a hard-coded list", async () => {
    const { costBpsInfo, INFO } = await import("./info");
    expect(costBpsInfo({ "3": 9, "1": 2, "2": 4 }).text).toContain("from the site's cost file (2, 4 or 9 bp);");
    expect(costBpsInfo(undefined)).toBe(INFO.cost_bps);
    expect(INFO.cost_bps.text).not.toMatch(/\d bp\)/);
  });
});
