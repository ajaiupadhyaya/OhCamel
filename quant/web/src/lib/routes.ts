/**
 * The app's page registry — the single source for the router, sidebar and command palette.
 * Page components live in src/pages/<Name>.tsx and are lazily loaded (one chunk per page).
 * Array order = sidebar order: Markets · Portfolio Lab, Optimizer, Strategy Lab ·
 * Volatility, Rates & Macro, Company · Live Engine, Methodology.
 */
import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { IconName } from "../components/Icon";

/** Sidebar groups, in display order. */
export const NAV_SECTIONS = ["Home", "Analyze", "Explore", "About"] as const;
export type NavSection = (typeof NAV_SECTIONS)[number];

export interface RouteDef {
  path: string;
  /** Link target for nav (for parameterised routes, a sensible default). */
  to: string;
  title: string;
  /** One line for the palette / placeholder. */
  blurb: string;
  icon: IconName;
  nav: boolean;
  /** Sidebar group. "Home" renders without a heading, above the rest. */
  section?: NavSection;
  keywords?: string;
  component: LazyExoticComponent<ComponentType>;
}

export const ROUTES: RouteDef[] = [
  { path: "/", to: "/", title: "Markets", icon: "markets", nav: true, section: "Home", blurb: "Today's market: indices, sectors, rates, credit, commodities and volatility.", keywords: "home overview heatmap sectors", component: lazy(() => import("../pages/Markets")) },
  { path: "/portfolio", to: "/portfolio", title: "Portfolio Lab", icon: "portfolio", nav: true, section: "Analyze", blurb: "Build or import a portfolio and study its performance, risk, factors and stress.", keywords: "holdings risk var factors stress 13f", component: lazy(() => import("../pages/PortfolioLab")) },
  { path: "/optimize", to: "/optimize", title: "Optimizer", icon: "optimize", nav: true, section: "Analyze", blurb: "Mean-variance, risk parity, HRP and Black–Litterman with constraints and walk-forward tests.", keywords: "frontier hrp black litterman weights", component: lazy(() => import("../pages/Optimizer")) },
  { path: "/research", to: "/research", title: "Strategy Lab", icon: "research", nav: true, section: "Analyze", blurb: "Backtest strategies from the literature and diagnose overfitting.", keywords: "backtest strategy pbo deflated sharpe", component: lazy(() => import("../pages/StrategyLab")) },
  { path: "/options", to: "/options", title: "Volatility", icon: "options", nav: true, section: "Explore", blurb: "Option chains, smiles, the vol surface, risk-neutral densities and payoffs.", keywords: "options iv surface svi skew greeks", component: lazy(() => import("../pages/Volatility")) },
  { path: "/macro", to: "/macro", title: "Rates & Macro", icon: "macro", nav: true, section: "Explore", blurb: "The yield curve, its history and factors, recession odds and regimes.", keywords: "yield curve fred treasury recession taylor", component: lazy(() => import("../pages/Macro")) },
  { path: "/company/:ticker", to: "/company", title: "Company", icon: "company", nav: true, section: "Explore", blurb: "Financial statements, ratios, quality scores and an interactive DCF.", keywords: "fundamentals dcf statements sec", component: lazy(() => import("../pages/Company")) },
  { path: "/ticker/:ticker", to: "/ticker/SPY", title: "Ticker", icon: "ticker", nav: false, blurb: "Price history, volume, return statistics and realized volatility.", keywords: "chart price", component: lazy(() => import("../pages/Ticker")) },
  { path: "/engine", to: "/engine", title: "Live Engine", icon: "engine", nav: true, section: "About", blurb: "The OCaml real-time risk engine and its live dependency graph.", keywords: "ocaml incremental realtime", component: lazy(() => import("../pages/Engine")) },
  { path: "/methodology", to: "/methodology", title: "Methodology", icon: "book", nav: true, section: "About", blurb: "Every model, formula, assumption and reference.", keywords: "docs references formulas", component: lazy(() => import("../pages/Methodology")) },
];

export const NotFoundPage = lazy(() => import("../pages/NotFound"));
