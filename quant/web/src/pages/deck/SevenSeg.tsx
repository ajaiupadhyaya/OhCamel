/**
 * Seven-segment digits drawn as SVG bars, lit and unlit — a real display shows its dark
 * segments faintly (Figure 2's `digit()`). Shared by the targeting computer's readout and the
 * lamp panel's counters. Glyphs come from segments.ts.
 */
import { litSegments } from "./segments";

const SEG_KEYS = ["a", "b", "c", "d", "e", "f", "g"] as const;

/** Width of a string drawn at digit width `w` with `gap` between cells (":" is half a cell). */
export function segWidth(text: string, w = 20, gap = 8): number {
  if (!text.length) return 0;
  let total = 0;
  for (const ch of text) total += ch === ":" ? w * 0.5 : w;
  return total + (text.length - 1) * gap;
}

function Digit({ ch, x, y, w, h }: { ch: string; x: number; y: number; w: number; h: number }) {
  const t = w * 0.18;
  const half = h / 2;
  const v = half - t * 1.5;
  const geo: Record<(typeof SEG_KEYS)[number], [number, number, number, number]> = {
    a: [x + t, y, w - 2 * t, t],
    b: [x + w - t, y + t, t, v],
    c: [x + w - t, y + half + t / 2, t, v],
    d: [x + t, y + h - t, w - 2 * t, t],
    e: [x, y + half + t / 2, t, v],
    f: [x, y + t, t, v],
    g: [x + t, y + half - t / 2, w - 2 * t, t],
  };
  const lit = litSegments(ch);
  return (
    <>
      {SEG_KEYS.map((k) => {
        const [gx, gy, gw, gh] = geo[k];
        return <rect key={k} x={gx} y={gy} width={gw} height={gh} rx={t / 3} className={lit.includes(k) ? "dk-seg on" : "dk-seg"} />;
      })}
    </>
  );
}

function Colon({ x, y, w, h }: { x: number; y: number; w: number; h: number }) {
  const t = w * 0.36;
  const cx = x + w * 0.25 - t / 2;
  return (
    <>
      <rect x={cx} y={y + h * 0.28} width={t} height={t} rx={t / 4} className="dk-seg on" />
      <rect x={cx} y={y + h * 0.66} width={t} height={t} rx={t / 4} className="dk-seg on" />
    </>
  );
}

/** A run of seven-segment cells starting at (x, y). Decorative: label the parent SVG. */
export function SevenSeg({ text, x, y, w = 20, h = 34, gap = 8, className }: { text: string; x: number; y: number; w?: number; h?: number; gap?: number; className?: string }) {
  let cx = x;
  const cells = [...text].map((ch, i) => {
    const cellW = ch === ":" ? w * 0.5 : w;
    const node = ch === ":" ? <Colon key={i} x={cx} y={y} w={w} h={h} /> : <Digit key={i} ch={ch} x={cx} y={y} w={w} h={h} />;
    cx += cellW + gap;
    return node;
  });
  return (
    <g className={className} aria-hidden="true">
      {cells}
    </g>
  );
}
