/**
 * Instrument A — the targeting computer (after Figure 2 / the 1977 trench-run display): the
 * book's limits as gates in an amber tunnel. The fullest limit still ahead is the target and
 * the red seven-segment readout is its room to spare; breaches have passed the screen and are
 * red rails; limits that cannot be evaluated are one dashed lilac gate beyond the vanishing
 * point. The layout is scope.ts; this file only draws it.
 *
 * Geometry changes only when readings change; alarm fields stay steady rather than flashing.
 */
import { useMemo } from "react";
import { useMediaQuery } from "../../lib/hooks";
import { scopeGeometry } from "./fit";
import { SevenSeg } from "./SevenSeg";
import { UNKNOWN_DEPTH, layout, scaleOf, type ScopeLimit } from "./scope";
import { useBox } from "./useBox";

export function useReducedMotion(): boolean {
  return useMediaQuery("(prefers-reduced-motion: reduce)");
}

export function TargetingComputer({ limits, unevaluated, stale = false, onInspect }: { limits: ScopeLimit[]; unevaluated: string[]; stale?: boolean; onInspect: (text: string) => void }) {
  const m = useMemo(() => layout(limits, unevaluated, () => stale), [limits, unevaluated, stale]);

  const readout = m.rails.length ? layout(limits.filter(l => l.breached), [], () => stale).readout : m.readout;
  const [box, size] = useBox<HTMLDivElement>({ width: 560, height: 340 });
  const g = scopeGeometry(size.width, size.height, readout.digits);
  const { x0, y0, x1, y1, cx, cy, hw, hh } = g;
  const gateRect = (s: number) => ({ x: cx - hw * s, y: cy - hh * s, width: 2 * hw * s, height: 2 * hh * s });
  const target = m.gates.find((t) => t.target);
  const tScale = target ? target.scale : 1;
  const railLabelX = x0 + 14 + m.rails.length * 10 + 4;
  const n = readout.digits.length;
  const us = scaleOf(UNKNOWN_DEPTH);
  const rd = g.readout;

  return (
    <div className="dk-scope">
      <div className={`dk-screen${m.rails.length ? " dk-alarm" : ""}`}>
        <div className="dk-fit dk-fit-scope" ref={box}>
        <svg width={g.w} height={g.h} viewBox={`0 0 ${g.w} ${g.h}`} role="img" aria-label={m.summary}>
          <g className="dk-persp">
            {g.persp.map(([x, y], i) => (
              <line key={i} x1={x} y1={y} x2={cx} y2={cy} />
            ))}
          </g>
          <rect x={x0 + 0.5} y={y0 + 0.5} width={x1 - x0 - 1} height={y1 - y0 - 1} rx={8} className="dk-frame" />

          {m.unknown.length > 0 && (
            <>
              <rect {...gateRect(us)} className="dk-gate unknown" />
              <text x={cx + hw * us + 5} y={cy + 4} className="dk-unknown-q">
                ?
              </text>
            </>
          )}

          {m.gates.map((gt) => (
            <rect key={gt.name} {...gateRect(gt.scale)} className={`dk-gate${gt.target ? " target" : ""}${gt.stale ? " stale" : ""}`} />
          ))}
          {target && (
            <text x={cx - hw * tScale + 6} y={cy - hh * tScale + 16} className={`dk-gate-label${target.stale ? " stale" : ""}`}>
              {target.name} {target.pct}
            </text>
          )}

          {m.rails.map((rl, i) => {
            const dx = 12 + i * 10;
            return (
              <g key={rl.name} className={`dk-rail${rl.stale ? " stale" : ""}`}>
                <line x1={x0 + dx} y1={y0} x2={x0 + dx} y2={y1} />
                <line x1={x1 - dx} y1={y0} x2={x1 - dx} y2={y1} />
                <text x={railLabelX} y={y1 - 8 - i * 14} className="dk-rail-label">
                  {rl.label}
                </text>
              </g>
            );
          })}
          {m.railsHidden > 0 && (
            <text x={railLabelX} y={y1 - 8 - m.rails.length * 14} className="dk-rail-label">
              +{m.railsHidden} more over
            </text>
          )}

          <g className={`dk-readout${readout.over ? " over" : ""}${readout.stale ? " stale" : ""}`}>
            <rect x={rd.x + 0.5} y={rd.y + 0.5} width={rd.w - 1} height={rd.h - 1} rx={4} className="dk-readout-box" />
            <SevenSeg text={readout.digits} x={cx - rd.seg.width / 2} y={rd.y + (rd.h - rd.seg.h) / 2} w={rd.seg.w} h={rd.seg.h} gap={rd.seg.gap} />
          </g>
          <text x={g.caption.x} y={g.caption.y} textAnchor="middle" className="dk-caption">
            {readout.caption}
          </text>
          <desc>{n ? `Readout ${readout.digits}${readout.unit ? ` ${readout.unit}` : ""}` : ""}</desc>
        </svg>
        </div>
      </div>

      <ul className="dk-scope-lamps" aria-label="Limits, fullest first on the screen; listed in the book's order">
        {m.lamps.map((l) => (
          <li key={l.name} className={`${l.state}${l.stale ? " stale" : ""}${l.name === m.target ? " target" : ""}`}>
            <span className="dk-lamp" aria-hidden="true" />
            <button className="dk-lamp-name" onClick={() => { const v = limits.find(x => x.name === l.name); onInspect(v ? `${l.name}: ${v.observed?.toPrecision(4) ?? "unknown"} / ${v.threshold.toPrecision(4)} ${v.unit} · ${l.pct}${v.breached ? " BREACHED" : " utilized"}` : `${l.name}: cannot be evaluated`); }}>{l.name}</button>
            <span className="dk-lamp-pct num">
              {l.state === "unknown" ? "?" : l.pct}
              <span className="sr-only">{l.state === "over" ? " — breached" : l.state === "unknown" ? " — cannot be evaluated" : l.name === m.target ? " — nearest limit" : ""}</span>
            </span>
          </li>
        ))}
        {!m.lamps.length && <li className="unknown">No limits.</li>}
      </ul>
    </div>
  );
}
