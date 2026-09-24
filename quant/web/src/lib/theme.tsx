/**
 * Theme: "light" | "dark" | "system", persisted to localStorage ("ohcamel.theme").
 * `resolved` is what is actually on screen. Charts subscribe to `resolved` and
 * rebuild their template from the CSS tokens via readTokens().
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type ThemeMode = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const KEY = "ohcamel.theme";

interface ThemeCtx {
  mode: ThemeMode;
  resolved: ResolvedTheme;
  setMode: (m: ThemeMode) => void;
  toggle: () => void;
}

const Ctx = createContext<ThemeCtx | null>(null);

function systemPref(): ResolvedTheme {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function readStored(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark") return v;
  } catch {
    /* storage blocked */
  }
  return "system";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(readStored);
  const [sys, setSys] = useState<ResolvedTheme>(systemPref);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => setSys(mq.matches ? "dark" : "light");
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  const resolved: ResolvedTheme = mode === "system" ? sys : mode;

  useEffect(() => {
    const root = document.documentElement;
    if (mode === "system") delete root.dataset.theme;
    else root.dataset.theme = mode;
    try {
      if (mode === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, mode);
    } catch {
      /* ignore */
    }
  }, [mode]);

  const setMode = useCallback((m: ThemeMode) => setModeState(m), []);
  const toggle = useCallback(() => setModeState((m) => ((m === "system" ? systemPref() : m) === "dark" ? "light" : "dark")), []);

  const value = useMemo(() => ({ mode, resolved, setMode, toggle }), [mode, resolved, setMode, toggle]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTheme must be used inside <ThemeProvider>");
  return v;
}

/** Snapshot of the design tokens as concrete colour strings (for Plotly, canvas, SVG). */
export interface Tokens {
  bg: string;
  surface: string;
  surface2: string;
  surface3: string;
  text: string;
  text2: string;
  text3: string;
  rule: string;
  ruleStrong: string;
  accent: string;
  gain: string;
  loss: string;
  warn: string;
  unknown: string;
  categorical: string[];
  /** Neutral colour for series beyond the 8th (and "Other" buckets) — never repeat a palette hue. */
  other: string;
  diverging: string[]; // 7 stops, loss -> mid -> gain (for P&L-signed data)
  divergingNeutral: string[]; // 7 stops, ochre -> mid -> cornflower (signed but not good/bad, e.g. correlation)
  sequential: string[]; // 5 stops, light -> dark (in light mode)
  fontUi: string;
  fontMono: string;
  fontDisplay: string;
}

export function readTokens(): Tokens {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string) => cs.getPropertyValue(n).trim();
  return {
    bg: v("--bg"),
    surface: v("--surface"),
    surface2: v("--surface-2"),
    surface3: v("--surface-3"),
    text: v("--text"),
    text2: v("--text-2"),
    text3: v("--text-3"),
    rule: v("--rule"),
    ruleStrong: v("--rule-strong"),
    accent: v("--accent"),
    gain: v("--gain"),
    loss: v("--loss"),
    warn: v("--warn"),
    unknown: v("--unknown"),
    categorical: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => v(`--c${i}`)),
    other: v("--c-other") || v("--text-3"),
    diverging: ["--div-neg-3", "--div-neg-2", "--div-neg-1", "--div-mid", "--div-pos-1", "--div-pos-2", "--div-pos-3"].map(v),
    divergingNeutral: ["--dn-neg-3", "--dn-neg-2", "--dn-neg-1", "--div-mid", "--dn-pos-1", "--dn-pos-2", "--dn-pos-3"].map(v),
    sequential: [1, 2, 3, 4, 5].map((i) => v(`--seq-${i}`)),
    fontUi: v("--font-ui"),
    fontMono: v("--font-mono"),
    fontDisplay: v("--font-display"),
  };
}

/** Tokens that update when the theme flips. */
export function useTokens(): Tokens {
  const { resolved } = useTheme();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => readTokens(), [resolved]);
}

/** Number of distinct categorical palette slots. */
export const CATEGORICAL_SLOTS = 8;

/**
 * CSS colour for series slot `i` (0-based): `var(--c1)`…`var(--c8)`, then the neutral
 * `var(--c-other)` for every slot ≥ 8. Palette hues are never repeated — two series sharing a
 * hue would read as the same thing. Use for CSS (chips, dots, bars).
 */
export function seriesVar(i: number): string {
  return i >= 0 && i < CATEGORICAL_SLOTS ? `var(--c${i + 1})` : "var(--c-other)";
}

/** Concrete colour for series slot `i` from a token snapshot (Plotly/canvas). Same rule as seriesVar. */
export function seriesColor(t: Tokens, i: number): string {
  return i >= 0 && i < CATEGORICAL_SLOTS ? t.categorical[i] : t.other;
}
