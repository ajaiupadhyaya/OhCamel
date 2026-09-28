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
import { SevenSeg, segWidth } from "./SevenSeg";
import { UNKNOWN_DEPTH, layout, scaleOf, type ScopeLimit } from "./scope";

const VB_W = 600;
const VB_H = 340;
const X0 = 24;
const Y0 = 16;
const X1 = 576;
const Y1 = 246; // the screen's frame
const CX = 300;
const CY = 131;
const HW = 276;
const HH = 115; // centre and half-size
const PERSP: [number, number][] = [
  [X0, Y0],
  [X1, Y0],
  [X0, Y1],
  [X1, Y1],
  [162, Y0],
  [438, Y0],
  [162, Y1],
  [438, Y1],
];

const gateRect = (s: number) => ({ x: CX - HW * s, y: CY - HH * s, width: 2 * HW * s, height: 2 * HH * s });

export function useReducedMotion(): boolean {
  return useMediaQuery("(prefers-reduced-motion: reduce)");
}

export function TargetingComputer({ limits, unevaluated, stale = false, onInspect }: { limits: ScopeLimit[]; unevaluated: string[]; stale?: boolean; onInspect: (text: string) => void }) {
  const m = useMemo(() => layout(limits, unevaluated, () => stale), [limits, unevaluated, stale]);

  const readout = m.rails.length ? layout(limits.filter(l => l.breached), [], () => stale).readout : m.readout;
  const target = m.gates.find((g) => g.target);
  const tScale = target ? target.scale : 1;
  const railLabelX = X0 + 14 + m.rails.length * 12 + 4;
  const n = readout.digits.length;
  const total = segWidth(readout.digits);
  const boxW = Math.max(240, total + 44);
  const us = scaleOf(UNKNOWN_DEPTH);

  return (
    <div className="dk-scope">
      <div className={`dk-screen${m.rails.length ? " dk-alarm" : ""}`}>
        <svg viewBox={`0 0 ${VB_W} ${VB_H}`} role="img" aria-label={m.summary}>
          <g className="dk-persp">
            {PERSP.map(([x, y], i) => (
              <line key={i} x1={x} y1={y} x2={CX} y2={CY} />
            ))}
          </g>
          <rect x={X0} y={Y0} width={X1 - X0} height={Y1 - Y0} rx={14} className="dk-frame" />

          {m.unknown.length > 0 && (
            <>
              <rect {...gateRect(us)} className="dk-gate unknown" />
              <text x={CX + HW * us + 5} y={CY + 4} className="dk-unknown-q">
                ?
              </text>
            </>
          )}

          {m.gates.map((g) => (
            <rect key={g.name} {...gateRect(g.scale)} className={`dk-gate${g.target ? " target" : ""}${g.stale ? " stale" : ""}`} />
          ))}
          {target && (
            <text x={CX - HW * tScale + 6} y={CY - HH * tScale + 22} className={`dk-gate-label${target.stale ? " stale" : ""}`}>
              {target.name} {target.pct}
            </text>
          )}

          {m.rails.map((rl, i) => {
            const dx = 14 + i * 12;
            return (
              <g key={rl.name} className={`dk-rail${rl.stale ? " stale" : ""}`}>
                <line x1={X0 + dx} y1={Y0} x2={X0 + dx} y2={Y1} />
                <line x1={X1 - dx} y1={Y0} x2={X1 - dx} y2={Y1} />
                <text x={railLabelX} y={Y1 - 10 - i * 14} className="dk-rail-label">
                  {rl.label}
                </text>
              </g>
            );
          })}
          {m.railsHidden > 0 && (
            <text x={railLabelX} y={Y1 - 10 - m.rails.length * 14} className="dk-rail-label">
              +{m.railsHidden} more over
            </text>
          )}

          <g className={`dk-readout${readout.over ? " over" : ""}${readout.stale ? " stale" : ""}`}>
            <rect x={CX - boxW / 2} y={258} width={boxW} height={52} rx={14} className="dk-readout-box" />
            <SevenSeg text={readout.digits} x={CX - total / 2} y={267} />
          </g>
          <text x={CX} y={328} textAnchor="middle" className="dk-caption">
            {readout.caption}
          </text>
          <desc>{n ? `Readout ${readout.digits}${readout.unit ? ` ${readout.unit}` : ""}` : ""}</desc>
        </svg>
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
