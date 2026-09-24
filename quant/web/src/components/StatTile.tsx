/**
 * Headline numbers.
 *
 *   <StatGrid>
 *     <StatTile label="Sharpe" value={fmtNum(s, 2)} info="sharpe" />
 *     <StatTile label="CAGR" value={fmtPct(c)} delta={c - bench} deltaFormat={(d) => fmtBps(d, 0, { signed: true })} deltaLabel="vs SPY" />
 *     <StatTile label="Max drawdown" value={fmtPct(mdd)} tone="loss" />
 *   </StatGrid>
 *
 * `delta` colours automatically (gain > 0, loss < 0; `invert` flips for lower-is-better).
 * `value` may be a pre-formatted string or a number + `format`.
 * Values are never ellipsized: they shrink to fit the tile width (to ~60% of the size) and
 * wrap only as a last resort. `size="sm"` is a smaller type ramp for dense grids / long values.
 */
import { useLayoutEffect, useRef, type ReactNode } from "react";
import { fmtPct, signClass } from "../lib/format";
import { InfoTip, type InfoProp } from "./InfoTip";
import { Skeleton } from "./States";

export interface StatTileProps {
  label: ReactNode;
  value: ReactNode | number | null | undefined;
  format?: (v: number) => string;
  delta?: number | null;
  deltaFormat?: (d: number) => string;
  deltaLabel?: ReactNode;
  invert?: boolean;
  tone?: "gain" | "loss" | "warn" | "neutral" | "auto";
  info?: InfoProp;
  caption?: ReactNode;
  loading?: boolean;
  /** "sm" for dense grids / long values; all sizes shrink to fit and wrap rather than truncate. */
  size?: "sm" | "md" | "lg";
}

export function StatTile({ label, value, format, delta, deltaFormat, deltaLabel, invert, tone = "neutral", info, caption, loading, size = "md" }: StatTileProps) {
  const shown = typeof value === "number" ? (format ? format(value) : String(value)) : (value ?? "—");
  const valueRef = useFitText<HTMLDivElement>(loading ? null : shown);
  const autoTone = tone === "auto" && typeof value === "number" ? signClass(value, invert) : tone === "gain" || tone === "loss" || tone === "warn" ? tone : "";
  return (
    <div className={`oc-stat oc-stat-${size}`}>
      <div className="oc-stat-label">
        <span>{label}</span>
        <InfoTip info={info} label={typeof label === "string" ? label : undefined} size={13} />
      </div>
      {loading ? (
        <Skeleton height={size === "lg" ? 34 : size === "sm" ? 20 : 26} width="70%" />
      ) : (
        <div ref={valueRef} className={`oc-stat-value num ${autoTone}`}>{shown}</div>
      )}
      {(delta !== undefined && delta !== null && Number.isFinite(delta)) || caption ? (
        <div className="oc-stat-foot">
          {delta !== undefined && delta !== null && Number.isFinite(delta) && (
            <span className={`oc-stat-delta num ${signClass(delta, invert)}`}>
              {delta > 0 ? "▲" : delta < 0 ? "▼" : "•"} {(deltaFormat ?? ((d) => fmtPct(d, 2, { signed: true })))(delta)}
            </span>
          )}
          {deltaLabel && <span className="subtle">{deltaLabel}</span>}
          {caption && <span className="subtle">{caption}</span>}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Shrink an element's font until its (single-line) content fits its width, re-checking on
 * resize. Falls back to wrapping (`.oc-stat-value-wrap`) at the minimum scale.
 */
function useFitText<E extends HTMLElement>(dep: unknown, minScale = 0.6) {
  const ref = useRef<E>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fit = () => {
      el.style.fontSize = "";
      el.classList.remove("oc-stat-value-wrap");
      if (el.scrollWidth <= el.clientWidth + 1) return;
      const base = parseFloat(getComputedStyle(el).fontSize) || 16;
      let size = base;
      while (el.scrollWidth > el.clientWidth + 1 && size > base * minScale) {
        size = Math.max(base * minScale, size * 0.92);
        el.style.fontSize = `${size}px`;
      }
      if (el.scrollWidth > el.clientWidth + 1) el.classList.add("oc-stat-value-wrap");
    };
    fit();
    let live = true;
    document.fonts?.ready.then(() => live && fit()); // mono webfont may load after first fit
    if (typeof ResizeObserver === "undefined") return () => void (live = false);
    let w = el.parentElement?.clientWidth ?? 0;
    const ro = new ResizeObserver(() => {
      const nw = el.parentElement?.clientWidth ?? 0;
      if (nw !== w) {
        w = nw;
        fit();
      }
    });
    if (el.parentElement) ro.observe(el.parentElement);
    return () => {
      live = false;
      ro.disconnect();
    };
  }, [dep, minScale]);
  return ref;
}

/** Responsive row of StatTiles with hairline separators. `min` = min tile width in px. */
export function StatGrid({ children, min = 150, className }: { children: ReactNode; min?: number; className?: string }) {
  return (
    <div className={`oc-statgrid ${className ?? ""}`} style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))` }}>
      {children}
    </div>
  );
}
