import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../charts/theme", async (orig) => ({
  ...(await orig<typeof import("../../charts/theme")>()),
  useChartTheme: () => new Proxy({}, { get: () => "" }),
}));
import { ThemeProvider } from "../../lib/theme";
import { IntegrityReport, ScreenReport, ShadowReport, annVol, shadowWide, type ShadowScore } from "./Watch";

function render(node: ReactNode) {
  return renderToStaticMarkup(
    <ThemeProvider>
      <MemoryRouter>{node}</MemoryRouter>
    </ThemeProvider>,
  );
}

const scores: ShadowScore[] = [
  { symbol: "SPY", model: "kronos", n: 25, qlike: -7.9, rank: 5 },
  { symbol: "SPY", model: "ewma", n: 25, qlike: -8.7, rank: 1 },
];

describe("Watch", () => {
  it("pivots scores by name and reads a variance as annualised volatility", () => {
    expect(shadowWide(scores)).toEqual([{ symbol: "SPY", n: 25, kronos: -7.9, ewma: -8.7 }]);
    expect(annVol(1e-4)).toBeCloseTo(Math.sqrt(252) * 0.01, 10);
    expect(annVol(null)).toBeNull();
    expect(annVol(-1)).toBeNull();
  });

  it("leads the shadow with its status: failed, shadow, sizes nothing", () => {
    const html = render(<ShadowReport scores={scores} pooled={{ days: 25, stat: 2.1, p_one_sided: 0.98 }} tonight={[{ symbol: "SPY", asof: "2026-10-16", model: "kronos", var: 1e-4 }]} asof="2026-10-16" />);
    expect(html).toContain("SHADOW · FAILED EXP-K01 · SIZES NOTHING");
    expect(html).toContain("KRONOS");
    expect(html).toContain("0.159"); // sqrt(252 * 1e-4)
  });

  it("says when nothing is realised yet rather than showing a number", () => {
    const html = render(<ShadowReport scores={[]} pooled={{ days: 0 }} tonight={[]} asof={null} />);
    expect(html).toContain("NO REALISED DAYS YET");
  });

  it("separates defects from large moves worth a look", () => {
    const html = render(
      <IntegrityReport
        findings={[
          { source: "warehouse", ticker: "XLE", date: "2025-12-05", check: "split_suspect", detail: "close moved -50.2%" },
          { source: "warehouse", ticker: "GLD", date: "2026-01-30", check: "jump", detail: "adjusted move -10.3%" },
        ]}
      />,
    );
    expect(html).toContain("SPLIT SUSPECT");
    expect(html).toContain("LARGE MOVES AND STALE SERIES");
  });

  it("states an empty ledger instead of an empty table", () => {
    expect(render(<ScreenReport rows={[]} />)).toContain("NO TRIALS IN THE LEDGER");
  });
});
