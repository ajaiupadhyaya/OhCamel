/**
 * The app's page registry — the single source for the router, the function-code nav and the
 * command line. Page components live in src/pages/<Name>.tsx and are lazily loaded (one
 * chunk per page). Every route has a function code; the nav row shows the codes of `nav`
 * routes in NAV_CODES order, then any other nav codes in array order.
 */
import { lazy, type ComponentType, type LazyExoticComponent } from "react";

/** The nav row's order of function codes. Codes no route carries yet are skipped. */
export const NAV_CODES = ["FRONT", "MKTS", "RISK", "PORT", "OPT", "RSCH", "VOL", "RATES", "CO", "DECK", "SYS"] as const;

export interface RouteDef {
  path: string;
  /** Link target for nav (for parameterised routes, a sensible default). */
  to: string;
  title: string;
  /** One line for the placeholder. */
  blurb: string;
  /** Function code: the nav label and the command-line verb (e.g. "MKTS"). */
  code: string;
  nav: boolean;
  keywords?: string;
  component: LazyExoticComponent<ComponentType>;
}

export const ROUTES: RouteDef[] = [
  { path: "/", to: "/", title: "The Tape", code: "FRONT", nav: true, blurb: "Front page: indices, the curve, regime, risk atlas, farm, models, last night's compute, data freshness.", keywords: "home front page broadsheet today", component: lazy(() => import("../pages/Front")) },
  { path: "/markets", to: "/markets", title: "Markets", code: "MKTS", nav: true, blurb: "Indices, sectors, rates, credit, commodities and volatility.", keywords: "overview heatmap sectors", component: lazy(() => import("../pages/Markets")) },
  { path: "/risk", to: "/risk", title: "Risk", code: "RISK", nav: true, blurb: "VaR and ES across models, Euler split, backtests, GARCH; Monte Carlo atlas.", keywords: "var es expected shortfall euler garch atlas monte carlo", component: lazy(() => import("../pages/Risk")) },
  { path: "/portfolio", to: "/portfolio", title: "Portfolio Lab", code: "PORT", nav: true, blurb: "Holdings, performance, factors and stress.", keywords: "holdings factors stress 13f", component: lazy(() => import("../pages/PortfolioLab")) },
  { path: "/optimize", to: "/optimize", title: "Optimizer", code: "OPT", nav: true, blurb: "Mean-variance, risk parity, HRP and Black–Litterman with constraints and walk-forward tests.", keywords: "frontier hrp black litterman weights", component: lazy(() => import("../pages/Optimizer")) },
  { path: "/research", to: "/research", title: "Strategy Lab", code: "RSCH", nav: true, blurb: "Backtests from the literature and overfitting diagnostics.", keywords: "backtest strategy pbo deflated sharpe farm models", component: lazy(() => import("../pages/StrategyLab")) },
  { path: "/options", to: "/options", title: "Volatility", code: "VOL", nav: true, blurb: "Option chains, smiles, the vol surface, risk-neutral densities and payoffs.", keywords: "options iv surface svi skew greeks", component: lazy(() => import("../pages/Volatility")) },
  { path: "/macro", to: "/macro", title: "Rates & Macro", code: "RATES", nav: true, blurb: "The yield curve, its history and factors, recession odds and regimes.", keywords: "yield curve fred treasury recession taylor", component: lazy(() => import("../pages/Macro")) },
  { path: "/company/:ticker", to: "/company", title: "Company", code: "CO", nav: true, blurb: "Financial statements, ratios, quality scores and an interactive DCF.", keywords: "fundamentals dcf statements sec", component: lazy(() => import("../pages/Company")) },
  { path: "/deck", to: "/deck", title: "Flight Deck", code: "DECK", nav: true, blurb: "Book limits, risk and data feeds as live instruments.", keywords: "instruments cockpit limits radar live targeting lamps var session tape recorder", component: lazy(() => import("../pages/Deck")) },
  { path: "/system", to: "/system", title: "System", code: "SYS", nav: true, blurb: "Compute, engine, methodology and the ledger.", keywords: "status build ops index", component: lazy(() => import("../pages/System")) },
  { path: "/compute", to: "/compute", title: "Compute", code: "COMPUTE", nav: false, blurb: "Host, jobs, schedule and kernel benchmarks.", keywords: "hostd cpu jobs queue schedule kernels rust", component: lazy(() => import("../pages/Compute")) },
  { path: "/engine", to: "/engine", title: "Live Engine", code: "ENG", nav: false, blurb: "The OCaml real-time risk engine and its live dependency graph.", keywords: "ocaml incremental realtime", component: lazy(() => import("../pages/Engine")) },
  { path: "/methodology", to: "/methodology", title: "Methodology", code: "DOCS", nav: false, blurb: "Every model, formula, assumption and reference.", keywords: "docs references formulas", component: lazy(() => import("../pages/Methodology")) },
  { path: "/ledger", to: "/ledger", title: "Ledger", code: "LEDGER", nav: false, blurb: "What was built, what was cut, what is advisory, known limits, owner-held items.", keywords: "scope cut limitations advisory owner", component: lazy(() => import("../pages/Ledger")) },
  { path: "/ticker/:ticker", to: "/ticker/SPY", title: "Ticker", code: "GP", nav: false, blurb: "Price history, volume, return statistics and realized volatility.", keywords: "chart price", component: lazy(() => import("../pages/Ticker")) },
];

/** Nav routes in function-code order. */
export function navRoutes(routes: RouteDef[] = ROUTES): RouteDef[] {
  const rank = (r: RouteDef) => {
    const i = (NAV_CODES as readonly string[]).indexOf(r.code);
    return i < 0 ? NAV_CODES.length : i;
  };
  return routes
    .map((r, i) => ({ r, i }))
    .filter(({ r }) => r.nav)
    .sort((a, b) => rank(a.r) - rank(b.r) || a.i - b.i)
    .map(({ r }) => r);
}

export const NotFoundPage = lazy(() => import("../pages/NotFound"));
