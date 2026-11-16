import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../charts/theme", async (orig) => ({
  ...(await orig<typeof import("../../charts/theme")>()),
  useChartTheme: () => new Proxy({}, { get: () => "" }),
}));
import { ThemeProvider } from "../../lib/theme";
import { Checks, normalization } from "./DensityTab";
import type { Density } from "./types";

function render(node: ReactNode) {
  return renderToStaticMarkup(
    <ThemeProvider>
      <MemoryRouter>{node}</MemoryRouter>
    </ThemeProvider>,
  );
}

// the checks of a two-year SPY slice on the 2026-11-15 chain (integral 0.9971 on a grid that
// stops at ln(K/F) = -3, 2.93e-3 of the mass below it) and of a one-week slice (nothing beyond)
const checks = (integral: number, left: number, right: number): Density["checks"] => ({
  integral, tail_mass_left: left, tail_mass_right: right, mean: 760.0, forward: 760.1, mean_minus_forward: -0.1,
  negative_mass: 0, butterfly: { arbitrage_free: true, g_min: 0.01, k_at_g_min: 1.6, violation_k: [], k_range: [-3, 3] },
});
const density = (c: Density["checks"]) => ({ checks: c, forward: 760.1, discount: 0.92, dte: 795.7, expiry: "2029-01-19" }) as Density;

describe("Density checks: normalisation counts the mass beyond the grid", () => {
  it("adds both tail masses to the integral", () => {
    const n = normalization(checks(0.9871, 0.0129, 2.4e-6));
    expect(n.total).toBeCloseTo(1.0, 5);
    expect(n.ok).toBe(true);                          // the integral alone (0.9871) would fail
    expect(n.showTails).toBe(true);
  });
  it("fails when the total is off by more than 1%, and hides tails at or below 0.001", () => {
    const n = normalization(checks(1.02, 0.0005, 0.0));
    expect(n.ok).toBe(false);
    expect(n.showTails).toBe(false);
  });
  it("shows the total, the grid integral, and each tail mass when one exceeds 0.001", () => {
    const html = render(<Checks d={density(checks(0.99707, 2.93e-3, 2.42e-6))} />);
    expect(html).toContain("∫ q(K) dK + tails");
    expect(html).toContain("1.00000");
    expect(html).toContain("ON GRID 0.99707");
    expect(html).toContain("Mass below grid");
    expect(html).toContain("0.29%");
    expect(html).toContain("Mass above grid");
    expect(html).toContain("0.00%");
  });
  it("leaves the tail rows out when both are tiny", () => {
    const html = render(<Checks d={density(checks(1.0, 5e-8, 3e-10))} />);
    expect(html).toContain("∫ q(K) dK + tails");
    expect(html).not.toContain("Mass below grid");
    expect(html).not.toContain("Mass above grid");
  });
});
