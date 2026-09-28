/**
 * Instrument B — the radar: where the book's risk is, and what is moving today. A polar scope
 * (after the reticle displays in the 1977 cockpit shots): each holding is a blip at a fixed
 * bearing (alphabetical ticker order), at a range set by its share of VaR, sized by its live weight, green or
 * red by today's move, hollow when it hedges (negative component VaR). Geometry: radar.ts.
 *
 * Motion only on data: the sweep turns once each time a new reading arrives (keyed on the
 * quote timestamps and prices); never under prefers-reduced-motion.
 *
 * (Named RadarScope.tsx, not Radar.tsx: that would collide with the pure radar.ts under
 * extensionless imports on case-insensitive filesystems such as macOS's.)
 */
import { useMemo } from "react";
import { fmtPct, fmtSignedPct } from "../../lib/format";
import { radar, type BlipIn } from "./radar";
import { useReducedMotion } from "./TargetingComputer";

const S = 400; // viewBox side
const C = S / 2;
const R = 168; // rim radius
const SPOKES = 12;

export function Radar({ blips, readingKey, onInspect }: { blips: BlipIn[]; readingKey: string | null; onInspect: (text: string) => void }) {
  const reduced = useReducedMotion();
  const m = useMemo(() => radar([...blips].sort((a,b) => a.ticker.localeCompare(b.ticker))), [blips]);
  const px = (u: number) => C + u * R;

  return (
    <div className="dk-radar">
      <div className="dk-screen dk-screen-round">
        <svg viewBox={`0 0 ${S} ${S}`} role="group" aria-label={m.summary}>
          <circle cx={C} cy={C} r={R} className="dk-radar-rim" />
          <g className="dk-radar-grid">
            {Array.from({ length: SPOKES }, (_, i) => {
              const t = (i * 2 * Math.PI) / SPOKES;
              return <line key={i} x1={C} y1={C} x2={C + R * Math.sin(t)} y2={C - R * Math.cos(t)} />;
            })}
            {Array.from({ length: 72 }, (_, i) => {
              const t = (i * 2 * Math.PI) / 72;
              const inner = i % 6 === 0 ? R - 10 : R - 5;
              return <line key={`t${i}`} x1={C + inner * Math.sin(t)} y1={C - inner * Math.cos(t)} x2={C + R * Math.sin(t)} y2={C - R * Math.cos(t)} className="dk-radar-tick" />;
            })}
          </g>
          {m.rings.map((ring) => (
            <g key={ring.share}>
              <circle cx={C} cy={C} r={ring.range * R} className="dk-radar-ring" />
              <text x={C - 8} textAnchor="end" y={C - ring.range * R - 4} className="dk-radar-ring-label">
                {Math.round(ring.share * 100)}%
              </text>
            </g>
          ))}

          {/* the sweep: one turn per reading */}
          <g key={reduced ? "still" : (readingKey ?? "none")} className={`dk-sweep${reduced || !readingKey ? "" : " turn"}`} style={{ transformOrigin: `${C}px ${C}px` }}>
            <path d={`M${C},${C} L${C},${C - R} A${R},${R} 0 0,0 ${C - R * Math.sin(Math.PI / 7)},${C - R * Math.cos(Math.PI / 7)} Z`} className="dk-sweep-wedge" />
            <line x1={C} y1={C} x2={C} y2={C - R} className="dk-sweep-line" />
          </g>

          {m.blips.map((b) => {
            const cx = px(b.x);
            const cy = px(b.y);
            const r = Math.max(3, b.r * R);
            const lx = Math.max(42, Math.min(S - 42, cx + (b.x >= 0 ? r + 4 : -(r + 4))));
            return (
              <g key={b.ticker} role="button" tabIndex={0} aria-label={`Inspect ${b.ticker}`}
                onClick={() => onInspect(`${b.ticker}: ${fmtPct(b.pct_var,1)} of VaR · weight ${fmtPct(b.live_weight,1)} · today ${fmtSignedPct(b.change_pct,2)}`)}
                onKeyDown={e => {if (e.key === "Enter" || e.key === " ") {e.preventDefault(); onInspect(`${b.ticker}: ${fmtPct(b.pct_var,1)} of VaR · weight ${fmtPct(b.live_weight,1)} · today ${fmtSignedPct(b.change_pct,2)}`);}}}
                className={`dk-blip ${b.tone}${b.hedge ? " hedge" : ""}`}>
                <circle cx={cx} cy={cy} r={r} />
                {b.clamped && <line x1={px(b.x * 1.04)} y1={px(b.y * 1.04)} x2={px(b.x * 1.1)} y2={px(b.y * 1.1)} className="dk-blip-clamp" />}
                <text x={lx} y={cy + 4} textAnchor={b.x >= 0 ? "start" : "end"} className="dk-blip-label">
                  {b.ticker}
                </text>
              </g>
            );
          })}
          <circle cx={C} cy={C} r={2.5} className="dk-radar-centre" />
        </svg>
      </div>
      <p className="dk-radar-caption">RANGE = |SHARE OF VaR|<br />SIZE = |WEIGHT| · HOLLOW = HEDGE</p>
      <div className="dk-ticker-keys" aria-label="Inspect holding">
        {m.blips.map(b => <button key={b.ticker} onClick={() => onInspect(`${b.ticker}: ${fmtPct(b.pct_var, 1)} of VaR${b.clamped ? " (beyond rim)" : ""} · weight ${fmtPct(b.live_weight, 1)} · today ${fmtSignedPct(b.change_pct, 2)}${b.hedge ? " · hedge" : ""}`)}>{b.ticker}</button>)}
      </div>
      {m.unplaced.length > 0 && <p className="small dk-unknown-text">Not placed (no share of VaR could be computed): {m.unplaced.join(", ")}.</p>}
      <ul className="sr-only">
        {m.blips.map((b) => (
          <li key={b.ticker}>
            {b.ticker}: {fmtPct(b.pct_var, 1)} of VaR{b.clamped ? " (beyond the rim)" : ""}, weight {fmtPct(b.live_weight, 1)}, today {fmtSignedPct(b.change_pct, 2)}
            {b.hedge ? ", hedging" : ""}.
          </li>
        ))}
      </ul>
    </div>
  );
}
