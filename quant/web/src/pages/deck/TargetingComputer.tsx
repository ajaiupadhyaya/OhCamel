/**
 * Instrument A — the targeting computer (after Figure 2 / the 1977 trench-run display): the
 * book's limits as gates in an amber tunnel. The fullest limit still ahead is the target and
 * the red seven-segment readout is its room to spare; breaches have passed the screen and are
 * red rails; limits that cannot be evaluated are one dashed lilac gate beyond the vanishing
 * point. The layout is scope.ts; this file only draws it.
 *
 * Motion only on data: gates glide 250 ms to a new depth; a newly breached rail blinks three
 * times. Neither happens under prefers-reduced-motion (CSS and the hook below).
 */
import { useEffect, useMemo, useRef, useState } from "react";
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
const TWEEN_MS = 250;
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

export function TargetingComputer({ limits, unevaluated, stale = false }: { limits: ScopeLimit[]; unevaluated: string[]; stale?: boolean }) {
  const reduced = useReducedMotion();
  const m = useMemo(() => layout(limits, unevaluated, () => stale), [limits, unevaluated, stale]);

  // Gates: each glides from where it was last drawn to its new scale.
  const drawn = useRef<Record<string, number>>({});
  const [shown, setShown] = useState<Record<string, number>>({});
  useEffect(() => {
    const to: Record<string, number> = {};
    for (const g of m.gates) to[g.name] = g.scale;
    const from: Record<string, number> = {};
    for (const k of Object.keys(to)) from[k] = drawn.current[k] ?? to[k];
    const moving = Object.keys(to).some((k) => from[k] !== to[k]);
    if (reduced || !moving || typeof requestAnimationFrame === "undefined") {
      drawn.current = to;
      setShown(to);
      return;
    }
    let raf = 0;
    let start: number | null = null;
    const step = (now: number) => {
      if (start === null) start = now;
      const k = Math.min(1, (now - start) / TWEEN_MS);
      const e = 1 - Math.pow(1 - k, 3);
      const cur: Record<string, number> = {};
      for (const n of Object.keys(to)) cur[n] = from[n] + (to[n] - from[n]) * e;
      drawn.current = cur;
      setShown(cur);
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [m, reduced]);

  // Rails: blink only the ones that were not breached on the previous reading (none on load).
  const before = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<{ names: Set<string>; stamp: number }>({ names: new Set(), stamp: 0 });
  useEffect(() => {
    const now = new Set(m.rails.map((r) => r.name));
    const prev = before.current;
    before.current = now;
    const names = prev ? new Set([...now].filter((n) => !prev.has(n))) : new Set<string>();
    setFresh((f) => (names.size || f.names.size ? { names, stamp: f.stamp + 1 } : f));
  }, [m]);

  const target = m.gates.find((g) => g.target);
  const tScale = target ? (shown[target.name] ?? target.scale) : 1;
  const railLabelX = X0 + 14 + m.rails.length * 12 + 4;
  const n = m.readout.digits.length;
  const total = segWidth(m.readout.digits);
  const boxW = Math.max(240, total + 44);
  const us = scaleOf(UNKNOWN_DEPTH);

  return (
    <div className="dk-scope">
      <div className="dk-screen">
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
            <rect key={g.name} {...gateRect(shown[g.name] ?? g.scale)} className={`dk-gate${g.target ? " target" : ""}${g.stale ? " stale" : ""}`} />
          ))}
          {target && (
            <text x={CX - HW * tScale + 6} y={CY - HH * tScale + 22} className={`dk-gate-label${target.stale ? " stale" : ""}`}>
              {target.name} {target.pct}
            </text>
          )}

          {m.rails.map((rl, i) => {
            const isFresh = fresh.names.has(rl.name);
            const dx = 14 + i * 12;
            return (
              <g key={isFresh ? `${rl.name}:${fresh.stamp}` : rl.name} className={`dk-rail${rl.stale ? " stale" : ""}${isFresh ? " fresh" : ""}`}>
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

          <g className={`dk-readout${m.readout.over ? " over" : ""}${m.readout.stale ? " stale" : ""}`}>
            <rect x={CX - boxW / 2} y={258} width={boxW} height={52} rx={14} className="dk-readout-box" />
            <SevenSeg text={m.readout.digits} x={CX - total / 2} y={267} />
          </g>
          <text x={CX} y={328} textAnchor="middle" className="dk-caption">
            {m.readout.caption}
          </text>
          <desc>{n ? `Readout ${m.readout.digits}${m.readout.unit ? ` ${m.readout.unit}` : ""}` : ""}</desc>
        </svg>
      </div>

      <ul className="dk-scope-lamps" aria-label="Limits, fullest first on the screen; listed in the book's order">
        {m.lamps.map((l) => (
          <li key={l.name} className={`${l.state}${l.stale ? " stale" : ""}${l.name === m.target ? " target" : ""}`}>
            <span className="dk-lamp" aria-hidden="true" />
            <span className="dk-lamp-name">{l.name}</span>
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
