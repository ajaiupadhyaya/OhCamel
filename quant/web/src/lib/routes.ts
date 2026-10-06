/**
 * The app's page registry — the single source for the router, the function-code nav and the
 * command palette. Page components live in src/pages/<Name>.tsx and are lazily loaded (one
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
  /** One line for the palette / placeholder. */
  blurb: string;
  /** Function code: the nav label and the command-line verb (e.g. "MKTS"). */
  code: string;
  nav: boolean;
  keywords?: string;
  component: LazyExoticComponent<ComponentType>;
}

export const ROUTES: RouteDef[] = [
  { path: "/", to: "/", title: "Markets", code: "MKTS", nav: true, blurb: "Today's market: indices, sectors, rates, credit, commodities and volatility.", keywords: "home overview heatmap sectors", component: lazy(() => import("../pages/Markets")) },
  { path: "/deck", to: "/deck", title: "Flight Deck", code: "DECK", nav: true, blurb: "Your book's limits, risk and data feeds as live cockpit instruments.", keywords: "instruments cockpit limits radar live targeting lamps var session tape recorder", component: lazy(() => import("../pages/Deck")) },
  { path: "/portfolio", to: "/portfolio", title: "Portfolio Lab", code: "PORT", nav: true, blurb: "Build or import a portfolio and study its performance, risk, factors and stress.", keywords: "holdings risk var factors stress 13f", component: lazy(() => import("../pages/PortfolioLab")) },
  { path: "/optimize", to: "/optimize", title: "Optimizer", code: "OPT", nav: true, blurb: "Mean-variance, risk parity, HRP and Black–Litterman with constraints and walk-forward tests.", keywords: "frontier hrp black litterman weights", component: lazy(() => import("../pages/Optimizer")) },
  { path: "/research", to: "/research", title: "Strategy Lab", code: "RSCH", nav: true, blurb: "Backtest strategies from the literature and diagnose overfitting.", keywords: "backtest strategy pbo deflated sharpe", component: lazy(() => import("../pages/StrategyLab")) },
  { path: "/options", to: "/options", title: "Volatility", code: "VOL", nav: true, blurb: "Option chains, smiles, the vol surface, risk-neutral densities and payoffs.", keywords: "options iv surface svi skew greeks", component: lazy(() => import("../pages/Volatility")) },
  { path: "/macro", to: "/macro", title: "Rates & Macro", code: "RATES", nav: true, blurb: "The yield curve, its history and factors, recession odds and regimes.", keywords: "yield curve fred treasury recession taylor", component: lazy(() => import("../pages/Macro")) },
  { path: "/company/:ticker", to: "/company", title: "Company", code: "CO", nav: true, blurb: "Financial statements, ratios, quality scores and an interactive DCF.", keywords: "fundamentals dcf statements sec", component: lazy(() => import("../pages/Company")) },
  { path: "/ticker/:ticker", to: "/ticker/SPY", title: "Ticker", code: "GP", nav: false, blurb: "Price history, volume, return statistics and realized volatility.", keywords: "chart price", component: lazy(() => import("../pages/Ticker")) },
  { path: "/engine", to: "/engine", title: "Live Engine", code: "ENG", nav: true, blurb: "The OCaml real-time risk engine and its live dependency graph.", keywords: "ocaml incremental realtime", component: lazy(() => import("../pages/Engine")) },
  { path: "/methodology", to: "/methodology", title: "Methodology", code: "DOCS", nav: true, blurb: "Every model, formula, assumption and reference.", keywords: "docs references formulas", component: lazy(() => import("../pages/Methodology")) },
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
