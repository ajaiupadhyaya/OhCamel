import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ThemeProvider } from "../../lib/theme";
import { Current } from "./RecessionTab";
import type { RecessionOut } from "./types";

function render(node: ReactNode) {
  return renderToStaticMarkup(
    <ThemeProvider>
      <MemoryRouter>{node}</MemoryRouter>
    </ThemeProvider>,
  );
}

// The 12-month figures /api/macro/recession gave on 2026-11-19 (completion task 3.5 report): out of sample
// 0.0812 over 545 target months 1981-06..2026-10, the in-sample fit 0.0770 on the same months.
const d = {
  h: 12,
  models: { spread: { h: 12, params: { const: -0.4, spread: -0.7 }, std_errors: {}, pvalues: {}, loglik: -150, loglik_null: -240, nobs: 790, pseudo_r2_mcfadden: 0.36, pseudo_r2_estrella: 0.271, regressors: ["spread"] } },
  current: { origin: "2026-11-01", target: "2027-11-01", spread: 0.42, probability: 0.134 },
  oos: { h: 12, oos_brier: 0.0812, oos_hit_rate: 0.877, insample_brier: 0.077, insample_hit_rate: 0.897, baseline_brier: 0.0903, brier_skill: 0.1, base_hit_rate: 0.905, n_scored: 545, scored: { start: "1981-06-01", end: "2026-10-01" } },
  sample: { start: "1960-01-01", end: "2026-11-01", nobs: 790 },
} as unknown as RecessionOut;

describe("recession headline (completion task 3.5)", () => {
  const html = render(<Current d={d} h="12" />);

  it("leads with the out-of-sample Brier score, the in-sample one only as its caption", () => {
    const first = html.indexOf("OOS BRIER · 12M");
    expect(first).toBeGreaterThanOrEqual(0);
    expect(first).toBeLessThan(html.indexOf("P(REC) · 12M"));
    expect(html).toContain("0.081");
    expect(html).toContain("IN-SAMPLE 0.077");
    expect(html.indexOf("0.081")).toBeLessThan(html.indexOf("IN-SAMPLE 0.077"));
  });

  it("shows the computed real-time baseline and skill, not a hard-coded benchmark (fix round 1)", () => {
    expect(html).toContain("BASELINE 0.090");
    expect(html).toContain("SKILL +10%");
    expect(html).not.toContain("0.12");
  });

  it("shows each hit rate with the never-recession rate and says when it is below it (fix round 1)", () => {
    expect(html).toContain("HIT RATE · OOS");
    expect(html).toContain("88%");
    expect(html).toContain("NEVER-RECESSION 91%");
    expect(html).toContain("OOS BELOW IT");
    expect(html).toContain("IN-SAMPLE 90%, BELOW IT");
    const above = render(<Current d={{ ...d, oos: { ...d.oos, oos_hit_rate: 0.93 } } as RecessionOut} h="12" />);
    expect(above).toContain("OOS ABOVE IT");
  });

  it("labels the pseudo-R² as the in-sample fit's", () => {
    expect(html).toContain("R² · ESTRELLA · IN-SAMPLE");
  });
});
