/** Rates & Macro — page-local helpers: tenor axes, recession shading, value formatting, small widgets. */
import { useMemo, type ReactNode } from "react";
import type { Data } from "plotly.js";
import { Chart, Formula, InfoTip, mergeLayout, withAlpha } from "../../components";
import type { Info } from "../../lib/glossary";
import { EM_DASH, fmtCompact, fmtCurrency, fmtNum, fmtPct, parseDate, toIsoDate } from "../../lib/format";
import type { Tokens } from "../../lib/theme";
import type { Episode } from "./types";

// ------------------------------------------------------------------ tenors
/** 0.0833 -> "1M", 0.5 -> "6M", 2 -> "2Y", 0.75 -> "9M". */
export function tenorLabel(t: number | string): string {
  const x = typeof t === "string" ? parseFloat(t) : t;
  if (!Number.isFinite(x)) return String(t);
  if (x < 1 - 1e-9) return `${Math.round(x * 12)}M`;
  return `${+x.toFixed(2)}Y`;
}

export const STD_TENORS = [1 / 12, 0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30];

/**
 * Maturity x-axis on a log scale so the bill end isn't crushed, with ticks at the
 * standard Treasury tenors. Hover shows the tenor label.
 */
export function tenorAxis(range?: [number, number]) {
  const lo = range?.[0] ?? 1 / 12;
  const hi = range?.[1] ?? 30;
  const ticks = STD_TENORS.filter((t) => t >= lo * 0.99 && t <= hi * 1.01);
  return {
    type: "log",
    tickvals: ticks,
    ticktext: ticks.map(tenorLabel),
    range: [Math.log10(lo * 0.9), Math.log10(hi * 1.08)],
    showspikes: false,
    title: { text: "Maturity" },
  };
}

// ------------------------------------------------------------------ shading
/** NBER recession rectangles (month start → end of the trough month). */
export function recessionShapes(episodes: Episode[] | undefined, t: Tokens, from?: string) {
  return (episodes ?? [])
    .filter((e) => !from || e.end >= from)
    .map((e) => {
      const end = parseDate(e.end)!;
      const x1 = toIsoDate(new Date(end.getFullYear(), end.getMonth() + 1, 1));
      return { type: "rect", xref: "x", yref: "paper", x0: e.start, x1, y0: 0, y1: 1, fillcolor: withAlpha(t.text3, 0.14), line: { width: 0 }, layer: "below" };
    });
}

/** Runs of a boolean flag along a date index -> [start, end] pairs. */
export function runs(index: (string | number)[], flag: (i: number) => boolean): [string, string][] {
  const out: [string, string][] = [];
  let s: number | null = null;
  for (let i = 0; i < index.length; i++) {
    const f = flag(i);
    if (f && s === null) s = i;
    if (!f && s !== null) {
      out.push([String(index[s]), String(index[i])]);
      s = null;
    }
  }
  if (s !== null) out.push([String(index[s]), String(index[index.length - 1])]);
  return out;
}

// ------------------------------------------------------------------ values
/** Format a FRED value by its units string ("%", "pp", "% y/y", "thousands", "index", "USD millions", …). */
export function fmtUnits(v: number | null | undefined, units: string, opts: { signed?: boolean; change?: boolean } = {}): string {
  if (v == null || !Number.isFinite(v)) return EM_DASH;
  const u = units.toLowerCase();
  const signed = !!opts.signed;
  if (u.startsWith("%")) return opts.change ? `${fmtNum(v, 2, { signed })} pp` : fmtPct(v / 100, 2, { signed });
  if (u === "pp") return `${fmtNum(v, 2, { signed })} pp`;
  if (u === "thousands") return `${fmtNum(v, 0, { signed })}k`;
  if (u === "claims") return fmtCompact(v, 1).replace(/^(?=\d)/, signed && v > 0 ? "+" : "");
  if (u.startsWith("usd millions")) return fmtCurrency(v * 1e6, { compact: true, signed });
  if (u.startsWith("usd")) return fmtCurrency(v, { digits: 2, signed });
  return fmtNum(v, Math.abs(v) >= 100 ? 1 : 2, { signed });
}

/** Ordinal percentile label: 0.87 -> "87th". */
export function ordinal(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return EM_DASH;
  const n = Math.round(p * 100);
  const s = n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${s}`;
}

// ------------------------------------------------------------------ widgets
/** A thin 0–100% track with a marker: where today's value sits in its 10-year range. */
export function PercentileGauge({ value, title }: { value: number | null | undefined; title?: string }) {
  const ok = value != null && Number.isFinite(value);
  const pct = ok ? Math.min(1, Math.max(0, value)) * 100 : 0;
  const tone = !ok ? "" : pct >= 90 || pct <= 10 ? "mc-gauge-extreme" : "";
  return (
    <div className={`mc-gauge ${tone}`} title={title} aria-label={title}>
      <div className="mc-gauge-track">
        <span className="mc-gauge-q" style={{ left: "25%" }} />
        <span className="mc-gauge-q" style={{ left: "50%" }} />
        <span className="mc-gauge-q" style={{ left: "75%" }} />
        {ok && <span className="mc-gauge-dot" style={{ left: `${pct}%` }} />}
      </div>
    </div>
  );
}

/** Key–value list for model parameters and fit statistics. */
export function KV({ rows }: { rows: { k: ReactNode; v: ReactNode; info?: Info | string; muted?: boolean }[] }) {
  return (
    <dl className="mc-kv">
      {rows.map((r, i) => (
        <div key={i} className={`mc-kv-row ${r.muted ? "muted" : ""}`}>
          <dt>
            {r.k}
            {r.info && <InfoTip info={r.info} size={12} />}
          </dt>
          <dd className="num">{r.v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** "How it works" card: the model in one formula, what it tells you, and the paper. */
export function MethodCard({ title, children, formulas, refs }: { title: ReactNode; children: ReactNode; formulas?: string[]; refs?: string[] }) {
  return (
    <aside className="mc-method">
      <div className="eyebrow">How it works</div>
      <h4 className="mc-method-title">{title}</h4>
      <div className="mc-method-body">{children}</div>
      {formulas?.map((f) => (
        <div key={f} className="mc-method-formula">
          <Formula tex={f} />
        </div>
      ))}
      {refs && refs.length > 0 && (
        <ul className="mc-method-refs">
          {refs.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
    </aside>
  );
}

/** Small colour key (legend chip) for shaded backgrounds that Plotly's legend can't show. */
export function Swatch({ color, label, hatch }: { color: string; label: ReactNode; hatch?: boolean }) {
  return (
    <span className="mc-swatch">
      <span className={`mc-swatch-chip ${hatch ? "hatch" : ""}`} style={{ background: color }} />
      {label}
    </span>
  );
}

/** Control strip above a tab's panels. */
export function Controls({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mc-controls">
      <div className="mc-controls-main">{children}</div>
      {right && <div className="mc-controls-right">{right}</div>}
    </div>
  );
}

/** Earliest ISO date for a "last N years" window ending at `end` (or today). */
export function yearsBack(n: number | null, end?: string): string | undefined {
  if (n == null) return undefined;
  const d = end ? parseDate(end)! : new Date();
  return toIsoDate(new Date(d.getFullYear() - n, d.getMonth(), d.getDate()));
}

// ------------------------------------------------------------------ bars
/**
 * Grouped bars with raw d3 tick/hover formats (page-local; the shared BarChart also works
 * now). `unit` is a hover/tick suffix.
 */
export function Bars({ series, height = 260, unit = "", tick = ",.0f", hover = ",.1f", pct, layout }: { series: { name: string; x: (string | number)[]; y: (number | null)[] }[]; height?: number; unit?: string; tick?: string; hover?: string; pct?: boolean; layout?: Record<string, unknown> }) {
  const data = useMemo(
    () => (t: Tokens): Data[] =>
      series.map((s, i) => ({
        type: "bar",
        name: s.name,
        x: s.x,
        y: s.y,
        marker: { color: t.categorical[i % 8] },
        hovertemplate: `${series.length > 1 ? "<b>%{fullData.name}</b> " : ""}%{x}: %{y:${pct ? ".1%" : hover}}${unit}<extra></extra>`,
      })) as Data[],
    [series, hover, unit, pct],
  );
  const lay = useMemo(
    () => mergeLayout({ barmode: "group", showlegend: series.length > 1, hovermode: "closest", xaxis: { type: "category", showgrid: false, showspikes: false }, yaxis: { tickformat: pct ? ".0%" : tick, ticksuffix: unit, zeroline: true, side: "right" }, margin: { l: 8, r: 8, t: series.length > 1 ? 30 : 8, b: 28 } } as any, layout),
    [series.length, pct, tick, unit, layout],
  );
  return <Chart data={data} layout={lay} height={height} />;
}
