import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../charts/theme", async (orig) => ({
  ...(await orig<typeof import("../../charts/theme")>()),
  useChartTheme: () => new Proxy({}, { get: () => "" }),
}));
import { DataUnavailableError, type ApiError } from "../../lib/api";
import { ThemeProvider } from "../../lib/theme";
import { VerdictBlock } from "./CompareTab";
import { DEFAULTS, type OptConfig } from "./config";
import { MethodPicker } from "./MethodPicker";
import { OptProvider, QPanel } from "./shared";
import type { CompareOut, MethodSpec } from "./types";

const cfg = { ...DEFAULTS, tickers: ["SPY", "TLT"], current: {}, currentSource: "x", start: null, end: null, benchmark: "SPY" } as unknown as OptConfig;

function render(node: ReactNode) {
  return renderToStaticMarkup(
    <ThemeProvider>
      <MemoryRouter>
        <OptProvider value={{ cfg, set: () => {}, rfMissing: false }}>{node}</OptProvider>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const spec = (name: string, label: string, mu: boolean, rf: boolean, bounds: boolean) =>
  ({ name, label, description: "long prose that must not render", reference: "", needs_expected_returns: mu, needs_risk_free: rf, honours_constraints: bounds, parameters: [] }) as unknown as MethodSpec;

describe("MethodPicker", () => {
  it("is a ruled radio table of codes and needs, without the catalog's prose", () => {
    const html = render(<MethodPicker methods={[spec("hrp", "Hierarchical risk parity", false, false, false), spec("max_sharpe", "Maximum Sharpe", true, true, true)]} cfg={{ ...cfg, method: "hrp" }} set={() => {}} rfMissing={true} />);
    expect(html).toContain('role="radiogroup"');
    expect(html).toMatch(/aria-checked="true"[^>]*class="op-method active"/);
    expect(html).toContain("HRP");
    expect(html).toContain("MAX SR");
    expect(html).not.toContain("long prose");
    // the risk-free flag is marked when the market series is missing
    expect(html).toMatch(/class="op-method-flag num loss"[^>]*>RF</);
  });
});

describe("VerdictBlock (BACKTEST · 1/N)", () => {
  const d = {
    stats: [
      { method: "equal_weight", label: "1/N", sharpe: 0.8, cagr: 0.07, annual_vol: 0.1 },
      { method: "hrp", label: "HRP", sharpe: 0.9 },
      { method: "max_sharpe", label: "Max Sharpe", sharpe: 0.4 },
    ],
    tests: {
      hrp: { vs: "equal_weight", ledoit_wolf: { sharpe_diff_annual: 0.1, p_value: 0.4, se_annual: 0.1 } },
      max_sharpe: { vs: "equal_weight", ledoit_wolf: { sharpe_diff_annual: -0.4, p_value: 0.01, se_annual: 0.1 } },
    },
    equity: { index: ["2019-01-02", "2026-06-01"], columns: [], data: {} },
    rebalance_dates: ["2019-01-31"],
    method: { window: 504, cost_bps: 10, benchmark: "equal_weight" },
  } as unknown as CompareOut;
  it("leads with the verdict stamp, FAIL when nothing beats 1/N, and names the test", () => {
    const html = render(<VerdictBlock d={d} />);
    expect(html.indexOf("verdict-fail")).toBeGreaterThan(-1);
    expect(html.indexOf("verdict-fail")).toBeLessThan(html.indexOf("op-verdict-grid"));
    expect(html).toContain("0 OF 2 BEAT 1/N · LW HAC 5%");
    expect(html).toContain("0.80");
  });
});

describe("QPanel", () => {
  it("a missing risk-free series reads INSUFFICIENT DATA, never a number on an assumed rate", () => {
    const q = { error: new DataUnavailableError("risk-free rate unavailable", "/portfolio/optimize"), isError: true, isLoading: false, isFetching: false, data: undefined, refetch: () => {} } as unknown as UseQueryResult<number, ApiError>;
    const html = render(
      <QPanel<number> q={q} title="X">
        {(v) => <span>{v}</span>}
      </QPanel>,
    );
    expect(html).toContain("INSUFFICIENT DATA");
    expect(html).toContain("RISK-FREE UNAVAILABLE");
    expect(html).toContain("SET RF");
  });
});
