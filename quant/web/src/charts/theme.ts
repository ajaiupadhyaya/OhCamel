/**
 * Chart theme: the paper tape tokens as concrete colour strings for canvas and SVG.
 *
 * Read from the live stylesheet with getComputedStyle, so a chart always draws in the
 * theme on screen. useChartTheme() re-reads whenever the resolved theme flips (paper or
 * carbon), which is what makes every uPlot chart rebuild itself on toggle.
 */
import { useMemo } from "react";
import { useTheme } from "../lib/theme";

export interface ChartTheme {
  paper: string;
  paper2: string;
  ink: string;
  ink2: string;
  ink3: string;
  signal: string;
  /** Series slots --c1..--c8, in order. Slot 9 and beyond take `other`. */
  series: string[];
  other: string;
  /** Sequential ink density ramp, light to dark (5 stops). */
  seq: string[];
  /** Diverging ramp, signal (negative) to ink (positive), 7 stops. */
  div: string[];
  /** The "neutral" diverging ramp (signed quantities that are not good or bad). */
  divNeutral: string[];
  mono: string;
  sans: string;
  display: string;
}

export function readChartTheme(): ChartTheme {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string) => cs.getPropertyValue(n).trim();
  return {
    paper: v("--paper"),
    paper2: v("--paper-2"),
    ink: v("--ink"),
    ink2: v("--ink-2"),
    ink3: v("--ink-3"),
    signal: v("--signal"),
    series: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => v(`--c${i}`)),
    other: v("--c-other") || v("--ink-3"),
    seq: [1, 2, 3, 4, 5].map((i) => v(`--seq-${i}`)),
    div: ["--div-neg-3", "--div-neg-2", "--div-neg-1", "--div-mid", "--div-pos-1", "--div-pos-2", "--div-pos-3"].map(v),
    divNeutral: ["--dn-neg-3", "--dn-neg-2", "--dn-neg-1", "--div-mid", "--dn-pos-1", "--dn-pos-2", "--dn-pos-3"].map(v),
    mono: v("--font-mono") || "monospace",
    sans: v("--font-ui") || "sans-serif",
    display: v("--font-display") || "sans-serif",
  };
}

/** The chart theme, re-read on every theme change. */
export function useChartTheme(): ChartTheme {
  const { resolved } = useTheme();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => readChartTheme(), [resolved]);
}

/** Colour for series slot `i`: the eight slots in order, then the neutral `other`. Never repeats a hue. */
export function slotColor(t: ChartTheme, i: number): string {
  return i >= 0 && i < t.series.length ? t.series[i] : t.other;
}

/** Resolve "var(--token)" to the concrete colour on screen; anything else passes through. */
export function resolveColor(c: string): string {
  const m = /^var\((--[\w-]+)\)$/.exec(c.trim());
  if (!m) return c;
  return getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim() || c;
}

/** Text that sits in a series' colour must stay readable: the rule grey (ink-3) reads as ink-2. */
export function labelColor(t: ChartTheme, color: string): string {
  return color === t.ink3 || color === t.other ? t.ink2 : color;
}

function parseRgb(c: string): [number, number, number] | null {
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c.trim());
  if (hex) {
    const h = hex[1].length === 3 ? hex[1].split("").map((x) => x + x).join("") : hex[1];
    const n = parseInt(h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const rgb = /^rgba?\(([^)]+)\)$/.exec(c.trim());
  if (rgb) {
    const [r, g, b] = rgb[1].split(/[,\s/]+/).map(Number);
    if ([r, g, b].every(Number.isFinite)) return [r, g, b];
  }
  return null;
}

/** "#rrggbb" with alpha, as rgba(). Unknown colour strings pass through unchanged. */
export function withAlpha(color: string, a: number): string {
  const rgb = parseRgb(resolveColor(color));
  return rgb ? `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${a})` : color;
}

/** Piecewise-linear interpolation along a ramp of colour stops; `p` in [0, 1]. */
export function rampColor(stops: string[], p: number): string {
  const rgbs = stops.map(parseRgb);
  if (!rgbs.length || rgbs.some((x) => !x)) return stops[Math.round(Math.min(1, Math.max(0, p)) * (stops.length - 1))] ?? "";
  const q = Math.min(1, Math.max(0, p)) * (rgbs.length - 1);
  const i = Math.min(rgbs.length - 2, Math.floor(q));
  const f = q - i;
  const a = rgbs[i]!, b = rgbs[Math.min(rgbs.length - 1, i + 1)]!;
  const mix = (k: number) => Math.round(a[k] + (b[k] - a[k]) * f);
  return `rgb(${mix(0)}, ${mix(1)}, ${mix(2)})`;
}

/** Relative luminance (WCAG) of a colour string; 0.5 when the string is not a colour. */
export function luminance(c: string): number {
  const rgb = parseRgb(c);
  if (!rgb) return 0.5;
  const ch = rgb.map((x) => {
    const s = x / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}

/** A canvas font string at the device pixel ratio. */
export function canvasFont(family: string, px: number, weight = 400): string {
  const r = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
  return `${weight} ${Math.round(px * r)}px ${family}`;
}
