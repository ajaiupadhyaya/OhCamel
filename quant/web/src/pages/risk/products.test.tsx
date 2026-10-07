import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../charts/theme", async (orig) => ({
  ...(await orig<typeof import("../../charts/theme")>()),
  useChartTheme: () => new Proxy({}, { get: () => "" }),
}));
import { ThemeProvider } from "../../lib/theme";
import { CovLeague } from "../portfolio/CovLeague";
import { Atlas } from "./AtlasCells";

const frame = (rows: Record<string, unknown>[]) => {
  const columns = Object.keys(rows[0]);
  return { index: rows.map((_, i) => i), columns, data: Object.fromEntries(columns.map((c) => [c, rows.map((r) => r[c])])) };
};

function render(seed: (qc: QueryClient) => void, node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  seed(qc);
  return renderToStaticMarkup(
    <QueryClientProvider client={qc}>
      <ThemeProvider>
        <MemoryRouter>{node}</MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

const manifest = (kind: string, tables: string[], verdict?: string) => ({ manifest: { id: "A1", kind, data_asof: "2026-10-06", finished_at: "2026-10-07T06:00:00Z", tables, verdict, provenance: [], notes: [] }, stale: false });

describe("ATLAS cells (risk.mc_atlas)", () => {
  it("pivot FHS beside t-copula for the chosen alpha and horizon, with the artifact's asOf", () => {
    const html = render((qc) => {
      qc.setQueryData(["GET", "/artifacts/risk.mc_atlas/latest", {}], manifest("risk.mc_atlas", ["summary", "euler"]));
      qc.setQueryData(
        ["GET", "/artifacts/risk.mc_atlas/A1/summary", {}],
        frame([
          { book: "core", method: "fhs", alpha: 0.99, horizon: 1, var: 0.0201, es: 0.0262, n_paths: 1000000 },
          { book: "core", method: "t_copula", alpha: 0.99, horizon: 1, var: 0.0233, es: 0.031, n_paths: 1000000 },
        ]),
      );
      qc.setQueryData(["GET", "/artifacts/risk.mc_atlas/A1/euler", {}], frame([{ book: "core", ticker: "SPY", weight: 0.4, pct_es: 0.61, method: "fhs", alpha: 0.99, horizon: 1 }]));
    }, <Atlas seedAlpha="0.99" seedH="1" />);
    expect(html).toContain("2.01%");
    expect(html).toContain("2.33%");
    expect(html).toContain("+0.32%");
    expect(html).toContain("61.0%");
    expect(html).toContain("PATHS 1,000,000");
    expect(html).toMatch(/06 OCT|2026-10-06/);
  });
  it("an unknown table shape reads as such, never as numbers", () => {
    const html = render((qc) => {
      qc.setQueryData(["GET", "/artifacts/risk.mc_atlas/latest", {}], manifest("risk.mc_atlas", ["summary", "euler"]));
      qc.setQueryData(["GET", "/artifacts/risk.mc_atlas/A1/summary", {}], frame([{ foo: 1 }]));
      qc.setQueryData(["GET", "/artifacts/risk.mc_atlas/A1/euler", {}], frame([{ foo: 1 }]));
    }, <Atlas seedAlpha="0.99" seedH="1" />);
    expect(html).toContain("UNKNOWN TABLE SHAPE");
    expect(html).toContain("INSUFFICIENT DATA");
  });
});

describe("COV LEAGUE cell (cov.league)", () => {
  it("leads with the verdict and ranks estimators by out-of-sample min-var vol", () => {
    const html = render((qc) => {
      qc.setQueryData(["GET", "/artifacts/cov.league/latest", {}], manifest("cov.league", ["league"], "PASS"));
      qc.setQueryData(["GET", "/artifacts/cov.league/A1/league", {}], frame([{ estimator: "sample", oos_vol: 0.121, lw_p: null }, { estimator: "ledoit_wolf", oos_vol: 0.104, lw_p: 0.02 }]));
    }, <CovLeague />);
    expect(html.indexOf("PASS")).toBeGreaterThan(-1);
    expect(html.indexOf("PASS")).toBeLessThan(html.indexOf("LEDOIT_WOLF"));
    expect(html.indexOf("LEDOIT_WOLF")).toBeLessThan(html.lastIndexOf("SAMPLE"));
    expect(html).toContain("−1.70%");
  });
});
