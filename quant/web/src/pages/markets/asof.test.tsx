import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

// Charts read CSS custom properties at render; the node SSR test has no computed style.
vi.mock("../../charts/theme", async (orig) => ({
  ...(await orig<typeof import("../../charts/theme")>()),
  useChartTheme: () => new Proxy({}, { get: () => "" }),
}));
import { ThemeProvider } from "../../lib/theme";
import Markets from "../Markets";
import type { Overview } from "./data";

/** Global Constraint: every computed panel shows provenance and asOf in its header. */
const AS_OF = "2026-10-02T20:00:00Z";
const CROSS = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG", "GLD", "USO", "BTC-USD"];

const row = (ticker: string) => ({ ticker, name: ticker, error: null, last: 100, as_of: AS_OF, ret_1d: 0.01, ret_1w: 0.02, ret_1m: -0.01, ret_3m: 0.03, ret_ytd: 0.05, ret_1y: 0.1 });
const overview = (universe: string, tickers: string[], extra: Partial<Overview> = {}): Overview => ({ universe, label: universe, as_of: AS_OF, rows: tickers.map(row), ...extra });

function render() {
  const qc = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
  qc.setQueryData(["GET", "/market/overview", { universe: "us_equity_indices" }], overview("us_equity_indices", ["SPY", "QQQ"]));
  qc.setQueryData(["GET", "/market/overview", { universe: "volatility" }], overview("volatility", ["^VIX"]));
  qc.setQueryData(["GET", "/market/overview", { universe: "sectors" }], overview("sectors", ["XLK", "XLF", "XLE"]));
  qc.setQueryData(
    ["GET", "/market/overview", { tickers: CROSS.join(",") }],
    overview("custom", CROSS.slice(0, 2), { correlation_1y: { tickers: ["SPY", "QQQ"], matrix: [[1, 0.9], [0.9, 1]], n_obs: 250 } }),
  );
  qc.setQueryData(["GET", "/market/universes", {}], []);
  qc.setQueryData(["GET", "/health", {}], { offline: true });
  return renderToStaticMarkup(
    <QueryClientProvider client={qc}>
      <ThemeProvider>
        <MemoryRouter initialEntries={["/markets"]}>
          <Markets />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

/** The header markup of the Cell whose title starts with `title`. */
function head(html: string, title: string): string {
  const i = html.indexOf(`<h3 class="oc-panel-title">${title}`);
  expect(i, `Cell ${title} rendered`).toBeGreaterThan(-1);
  return html.slice(i, html.indexOf("</header>", i));
}

describe("Markets: every computed Cell dates its numbers", () => {
  const html = render();
  for (const title of ["Correlation · 1Y", "Ranked · 1D", "Detail", "Heatmap"]) {
    it(`${title} carries AS OF in its header`, () => {
      expect(head(html, title)).toContain(`AS OF <time dateTime="${AS_OF}">`);
    });
  }
});
