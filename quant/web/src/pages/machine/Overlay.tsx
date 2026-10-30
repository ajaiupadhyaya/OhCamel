/**
 * What sits on the canvas and follows it: the labels and the hover tooltip. Each holds its own state and takes its
 * updates through a `sink` the scene's handlers call, so a moving camera or pointer re-renders these and never the
 * panels. Everything shown is a text child.
 */
import { useEffect, useState, type CSSProperties, type MutableRefObject } from "react";
import { flushSync } from "react-dom";
import type { PlacedLabel } from "./scene-labels";
import type { GraphIndex } from "./web";

export type Sink<T extends unknown[]> = MutableRefObject<((...args: T) => void) | null>;

/** A CSS length with one decimal, enough for a label's place on screen. */
const px = (v: number): string => `${Math.round(v * 10) / 10}px`;

export function Labels({ sink }: { sink: Sink<[PlacedLabel[]]> }) {
  const [placed, setPlaced] = useState<PlacedLabel[]>([]);
  useEffect(() => {
    // The scene calls this inside its frame. flushSync commits the labels in that frame, so they move with the canvas
    // and not one frame behind it.
    sink.current = (p) => flushSync(() => setPlaced(p));
    return () => {
      sink.current = null;
    };
  }, [sink]);
  return (
    <div className="mx-labels" aria-hidden="true">
      {placed.map((l) => (
        <span key={l.key} className={`mx-lb ${l.cls}`} style={{ "--x": px(l.x), "--y": px(l.y) } as CSSProperties}>
          {l.text}
        </span>
      ))}
    </div>
  );
}

/** How far the tooltip sits from the pointer, and the room it keeps at the right edge. */
const TIP_GAP = 14;
const TIP_ROOM = 330;

export function Tooltip({ gi, sink }: { gi: GraphIndex; sink: Sink<[number | null, number, number]> }) {
  const [tip, setTip] = useState<{ i: number; x: number; y: number } | null>(null);
  useEffect(() => {
    sink.current = (i, x, y) => setTip(i === null ? null : { i, x, y });
    return () => {
      sink.current = null;
    };
  }, [sink]);

  const n = tip ? gi.out.nodes[tip.i] : undefined;
  if (!tip || !n) return null;
  const verdict = typeof n.m.verdict === "string" ? n.m.verdict : null;
  const track = typeof n.m.track === "string" ? n.m.track : null;
  return (
    <div className="mx-tip" style={{ "--x": px(Math.min(tip.x + TIP_GAP, window.innerWidth - TIP_ROOM)), "--y": px(tip.y + TIP_GAP) } as CSSProperties}>
      <b>{n.k === "series" ? n.l : n.n}</b>
      {n.k === "series" && n.n !== n.l && <div>{n.n}</div>}
      {verdict && <div className={/FAIL/.test(verdict) ? "fail" : "verdict"}>{verdict}</div>}
      {n.status === "missing" && <div className="verdict">missing</div>}
      <div className="dim">{[n.k, track, "click to select"].filter(Boolean).join(" · ")}</div>
    </div>
  );
}
