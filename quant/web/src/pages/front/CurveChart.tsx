/**
 * The Treasury par curve over tenor (log scale), today against 1M and 1Y ago, in plain SVG:
 * today a 1.5px ink line, 1M ink-2 dotted, 1Y ink-2 dashed; dotted --ink-3 gridlines, a solid
 * ink baseline, mono ticks and direct end labels. A readout strip above reads the tenor under
 * the pointer (10Y until the pointer moves).
 */
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { endLabels, niceTicks } from "../../charts/scales";
import { fmtPctPoints } from "../../lib/format";
import { tenorLabel } from "../macro/shared";

export interface CurveLine {
  key: string;
  label: string;
  tenors: number[];
  yields: number[];
}

const r1 = (v: number) => Math.round(v * 10) / 10;
const TICKS = [0.25, 1, 2, 5, 10, 30];
const PAD = { l: 8, r: 64, t: 8, b: 22 };
const STYLE: Record<string, { w: number; dash?: string; tone: string }> = {
  today: { w: 1.5, tone: "var(--ink)" },
  "1M": { w: 1.25, dash: "1.5 3", tone: "var(--ink-2)" },
  "1Y": { w: 1.25, dash: "5 4", tone: "var(--ink-2)" },
};

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.clientWidth);
    const ro = new ResizeObserver((e) => setW(Math.round(e[0]?.contentRect.width ?? 0)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export function CurveChart({ lines, height = 220 }: { lines: CurveLine[]; height?: number }) {
  const [ref, width] = useWidth();
  const today = lines.find((l) => l.key === "today") ?? lines[0];
  const [hover, setHover] = useState<number | null>(null);

  const geo = useMemo(() => {
    const all = lines.flatMap((l) => l.yields).filter(Number.isFinite);
    const tmin = Math.min(...lines.flatMap((l) => l.tenors));
    const tmax = Math.max(...lines.flatMap((l) => l.tenors));
    const yt = all.length ? niceTicks(Math.min(...all), Math.max(...all), 4) : [0, 1];
    const y0 = yt[0];
    const y1 = yt[yt.length - 1];
    const iw = Math.max(40, width - PAD.l - PAD.r);
    const ih = height - PAD.t - PAD.b;
    const lx0 = Math.log(tmin);
    const lx1 = Math.log(tmax);
    const x = (t: number) => PAD.l + ((Math.log(t) - lx0) / (lx1 - lx0 || 1)) * iw;
    const y = (v: number) => PAD.t + (1 - (v - y0) / (y1 - y0 || 1)) * ih;
    return { yt, x, y, iw, ih, tmin, tmax };
  }, [lines, width, height]);

  const idx = hover ?? Math.max(0, today.tenors.findIndex((t) => Math.abs(t - 10) < 1e-6));
  const tenorAt = today.tenors[idx];
  const readout = lines.map((l) => {
    const j = l.tenors.findIndex((t) => Math.abs(t - tenorAt) < 1e-6);
    return { k: l.label, v: j >= 0 ? fmtPctPoints(l.yields[j]) : "—" };
  });

  const labels = endLabels(
    lines.map((l) => ({ y: geo.y(l.yields[l.yields.length - 1]), text: l.key })),
    12,
  );

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left;
    let best = 0;
    today.tenors.forEach((t, i) => {
      if (Math.abs(geo.x(t) - px) < Math.abs(geo.x(today.tenors[best]) - px)) best = i;
    });
    setHover(best);
  };

  return (
    <div className="fp-curve" ref={ref}>
      <div className="oc-chart-readout" aria-live="off">
        <span className="oc-chart-readout-k">TENOR</span> <span className="oc-chart-readout-v">{tenorLabel(tenorAt)}</span>
        {readout.map((r) => (
          <span key={r.k}>
            {" · "}
            <span className="oc-chart-readout-k">{r.k}</span> <span className="oc-chart-readout-v">{r.v}</span>
          </span>
        ))}
      </div>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={`Treasury par curve: ${lines.map((l) => l.label).join(", ")}`} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
          {geo.yt.map((v) => (
            <g key={v}>
              <line x1={PAD.l} x2={PAD.l + geo.iw} y1={Math.round(geo.y(v)) + 0.5} y2={Math.round(geo.y(v)) + 0.5} className="fp-grid" />
              <text x={PAD.l + geo.iw + 4} y={geo.y(v)} className="fp-tick" dominantBaseline="middle">
                {fmtPctPoints(v, 1)}
              </text>
            </g>
          ))}
          {TICKS.filter((t) => t >= geo.tmin * 0.99 && t <= geo.tmax * 1.01).map((t) => (
            <g key={t}>
              <line x1={geo.x(t)} x2={geo.x(t)} y1={PAD.t + geo.ih} y2={PAD.t + geo.ih + 4} className="fp-axis" />
              <text x={geo.x(t)} y={height - 4} className="fp-tick" textAnchor="middle">
                {tenorLabel(t)}
              </text>
            </g>
          ))}
          <line x1={PAD.l} x2={PAD.l + geo.iw} y1={PAD.t + geo.ih + 0.5} y2={PAD.t + geo.ih + 0.5} className="fp-axis" />
          <line x1={geo.x(tenorAt)} x2={geo.x(tenorAt)} y1={PAD.t} y2={PAD.t + geo.ih} className="fp-cross" />
          {[...lines].reverse().map((l) => {
            const s = STYLE[l.key] ?? STYLE["1Y"];
            const d = l.tenors.map((t, i) => `${i ? "L" : "M"}${r1(geo.x(t))} ${r1(geo.y(l.yields[i]))}`).join(" ");
            return <path key={l.key} d={d} fill="none" stroke={s.tone} strokeWidth={s.w} strokeDasharray={s.dash} strokeLinejoin="round" />;
          })}
          {today.tenors.map((t, i) => (
            <rect key={t} x={geo.x(t) - 2} y={geo.y(today.yields[i]) - 2} width={4} height={4} className="fp-node" />
          ))}
          {labels.map((p) => {
            const l = lines.find((x) => x.key === p.text)!;
            return (
              <text key={l.key} x={PAD.l + geo.iw + 34} y={p.y} className={l.key === "today" ? "fp-end fp-end-today" : "fp-end"} dominantBaseline="middle">
                {l.key === "today" ? "NOW" : l.key}
              </text>
            );
          })}
        </svg>
      )}
    </div>
  );
}
