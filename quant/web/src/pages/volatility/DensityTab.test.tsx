import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../charts/theme", async (orig) => ({
  ...(await orig<typeof import("../../charts/theme")>()),
  useChartTheme: () => new Proxy({}, { get: () => "" }),
}));
import { ThemeProvider } from "../../lib/theme";
import { Checks, showTails } from "./DensityTab";
import type { Density } from "./types";

function render(node: ReactNode) {
  return renderToStaticMarkup(
    <ThemeProvider>
      <MemoryRouter>{node}</MemoryRouter>
    </ThemeProvider>,
  );
}

// the checks of the two-year SPY slice on the 2026-11-15 chain (grid integral 0.99707 on a grid that
// stops at ln(K/F) = -3, 2.93e-3 of the mass below it) and of a one-week slice (nothing beyond)
type Passed = Density["checks"]["passed"];
const PASS: Passed = { negative_mass: true, analytic: true, mean: true };
const checks = (integral: number, left: number, right: number, passed: Passed = PASS, analytic = integral): Density["checks"] => ({
  integral, tail_mass_left: left, tail_mass_right: right, integral_plus_tails: integral + left + right,
  analytic_integral: analytic, passed,
  mean: 760.0, forward: 760.1, mean_minus_forward: -0.1, negative_mass: 0,
  butterfly: { arbitrage_free: true, g_min: 0.0, k_at_g_min: 1.6, violation_k: [], k_range: [-3, 3] },
});
const density = (c: Density["checks"]) => ({ checks: c, forward: 760.1, discount: 0.92, dte: 795.7, expiry: "2029-01-19" }) as Density;
const fails = (html: string) => (html.match(/>FAIL</g) ?? []).length;  // shared.Check prints PASS / FAIL
const marks = (html: string) => (html.match(/>(PASS|FAIL)</g) ?? []).length;

describe("Density checks: only checks that can fail get a mark", () => {
  it("shows the tail masses as their own figures when one exceeds 0.001", () => {
    expect(showTails(checks(0.99707, 2.93e-3, 2.42e-6))).toBe(true);
    expect(showTails(checks(1.0, 5e-4, 1e-9))).toBe(false);
  });
  it("shows the grid integral against the closed form, each tail mass, and integral + tails as a plain figure", () => {
    const html = render(<Checks d={density(checks(0.99707, 2.93e-3, 2.42e-6, PASS, 0.9970696))} />);
    expect(html).toContain("Numerical cross-check");
    expect(html).toContain("CLOSED FORM 0.99707");
    expect(html).toContain("Mass below grid");
    expect(html).toContain("0.29%");
    expect(html).toContain("Mass above grid");
    expect(html).toContain("∫ q dK + tails");
    expect(html).toContain("1.00000");
    expect(fails(html)).toBe(0);
  });
  it("marks a failure from the API's checks even when integral + tails is exactly 1", () => {
    // Vogt-like: the density goes negative and the grid integral misses the closed form, yet the
    // telescoping sum is 1 -- the marks follow `passed`, not the sum
    const bad = checks(0.98, 0.0, 0.02, { negative_mass: false, analytic: false, mean: true }, 0.999);
    expect(bad.integral_plus_tails).toBeCloseTo(1.0, 12);
    const html = render(<Checks d={density(bad)} />);
    expect(fails(html)).toBe(2);
  });
  it("shows tail masses as plain figures with no mark, in one row when both are tiny", () => {
    const big = render(<Checks d={density(checks(0.99707, 2.93e-3, 2.42e-6))} />);
    const tiny = render(<Checks d={density(checks(1.0, -5e-8, 3e-10))} />);
    expect(tiny).toContain("Mass beyond grid");
    expect(tiny).not.toContain("Mass below grid");
    // the same four marks either way (cross-check, mean, negative mass, smile butterfly): none on a tail row
    expect(marks(big)).toBe(4);
    expect(marks(tiny)).toBe(4);
  });
});
