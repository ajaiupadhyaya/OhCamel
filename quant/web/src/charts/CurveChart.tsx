/**
 * A yield curve on uPlot: yield (percent points) over tenor on a log x axis.
 *
 *   <CurveChart lines={[{ key: "now", label: "NOW", tenors, yields }, { key: "1M", label: "1M", … }]} />
 *
 * The first line is the lead (1.5px ink, square nodes); the rest are ink-2 at 1.25px, dotted
 * then dashed. Dotted --ink-3 gridlines, a solid ink baseline, mono ticks, direct end labels
 * and a readout strip (TENOR 10Y · NOW 4.47% · 1M …) that follows the pointer and rests on 10Y.
 */
import { useMemo, useRef, type CSSProperties } from "react";
import type uPlot from "uplot";
import { endLabels, formatValue } from "./scales";
import { curveTable, fmtTenor, tenorSplits, type CurveLine } from "./curve";
import { DASH, MONO_CH, Readout, STRIP, UPlot, axisFont, niceRange, pxr, writeReadout, xRule, yAxis } from "./UPlot";
import { useChartTheme } from "./theme";

export type { CurveLine } from "./curve";

const STYLES: { width: number; dash?: number[] }[] = [{ width: 1.5 }, { width: 1.25, dash: DASH.dot }, { width: 1.25, dash: DASH.dash }, { width: 1.25, dash: DASH.dashdot }];

export function CurveChart({ lines, height = 260, ariaLabel }: { lines: CurveLine[]; height?: number; ariaLabel?: string }) {
  const t = useChartTheme();
  const readout = useRef<HTMLDivElement>(null);
  const table = useMemo(() => curveTable(lines), [lines]);
  const data = useMemo<uPlot.AlignedData>(() => [table.x, ...table.ys], [table]);
  const labels = lines.map((l) => l.label).join("|");
  const plotH = height - STRIP;

  const opts = useMemo(() => {
    const names = labels.split("|");
    const colors = names.map((_, i) => (i === 0 ? t.ink : t.ink2));
    const count = Math.max(2, Math.round(plotH / 56));
    const r = pxr();
    return (_width: number) => {
      const gutter = Math.ceil(Math.max(3, ...names.map((n) => n.length)) * MONO_CH) + 14;
      const show = (u: uPlot, idx: number | null | undefined) => {
        const xs = u.data[0];
        if (!xs.length) return writeReadout(readout.current, [{ k: "NO DATA", v: "" }]);
        const rest = xs.findIndex((x) => Math.abs(x - 10) < 1e-6);
        const i = idx ?? (rest >= 0 ? rest : xs.length - 1);
        writeReadout(readout.current, [{ k: "TENOR", v: fmtTenor(xs[i]) }, ...names.map((n, s) => ({ k: n, v: formatValue(u.data[s + 1][i] as number | null, "pctPoints", 2) }))]);
      };
      const o: Omit<uPlot.Options, "width" | "height"> = {
        legend: { show: false },
        padding: [10, gutter, 0, 0],
        cursor: { y: false, drag: { x: false, y: false }, points: { size: 6, width: 1.25, fill: t.paper, stroke: (_u: uPlot, s: number) => colors[s - 1] ?? t.ink } },
        scales: {
          x: { time: false, distr: 3, range: (_u, min, max) => [min * 0.92, max * 1.06] },
          y: { range: (_u, min, max) => niceRange(min, max, count, []) },
        },
        series: [
          {},
          ...names.map((n, i) => ({
            label: n,
            stroke: colors[i],
            width: STYLES[Math.min(i, STYLES.length - 1)].width,
            dash: STYLES[Math.min(i, STYLES.length - 1)].dash,
            spanGaps: true,
            points: i === 0 ? { show: true, size: 4, width: 0, fill: t.ink, stroke: t.ink } : { show: false },
          })),
        ],
        axes: [
          {
            stroke: t.ink2,
            font: axisFont(t),
            gap: 4,
            size: 24,
            grid: { show: false },
            ticks: { show: true, stroke: t.ink, width: 1, size: 4 },
            border: { show: false },
            splits: (_u, _i, min, max) => tenorSplits(min, max),
            // uPlot's default log-axis filter blanks every split that is not a power of ten
            filter: (_u, splits) => splits,
            values: (_u, splits) => splits.map(fmtTenor),
          },
          yAxis(t, "pctPoints", count, false),
        ],
        hooks: {
          ready: [(u) => show(u, null)],
          setData: [(u) => show(u, null)],
          setCursor: [(u) => show(u, u.cursor.idx)],
          draw: [
            (u) => {
              xRule(u, t);
              const { ctx, bbox } = u;
              const xs = u.data[0];
              const items: { y: number; text: string; x: number }[] = [];
              names.forEach((_n, s) => {
                const ys = u.data[s + 1];
                for (let i = xs.length - 1; i >= 0; i--) {
                  const v = ys[i];
                  if (v !== null && v !== undefined) {
                    items.push({ y: u.valToPos(v, "y", true), text: String(s), x: u.valToPos(xs[i], "x", true) });
                    break;
                  }
                }
              });
              const placed = endLabels(items, 12 * r);
              const floor = bbox.top + bbox.height;
              if (placed.length && placed[placed.length - 1].y > floor) {
                placed[placed.length - 1].y = floor;
                for (let i = placed.length - 2; i >= 0; i--) placed[i].y = Math.min(placed[i].y, placed[i + 1].y - 12 * r);
              }
              const right = bbox.left + bbox.width;
              ctx.save();
              ctx.setLineDash([]);
              ctx.font = `${Math.round(11 * r)}px ${t.mono}`;
              ctx.textBaseline = "middle";
              ctx.textAlign = "left";
              for (const p of placed) {
                const s = Number(p.text);
                const src = items.find((it) => it.text === p.text)!;
                ctx.strokeStyle = colors[s];
                ctx.lineWidth = r;
                ctx.beginPath();
                ctx.moveTo(Math.min(src.x, right), src.y);
                ctx.lineTo(right + 3 * r, src.y);
                ctx.lineTo(right + 6 * r, p.y);
                ctx.stroke();
                ctx.fillStyle = colors[s];
                ctx.fillText(names[s], right + 8 * r, p.y);
              }
              ctx.restore();
            },
          ],
        },
      };
      return o;
    };
  }, [t, labels, plotH]);

  return (
    <div className="oc-chart oc-chart-uplot oc-chart-fixed" style={{ "--chart-h": `${height}px` } as CSSProperties} role="img" aria-label={ariaLabel ?? `Yield curve: ${lines.map((l) => l.label).join(", ")}`}>
      <Readout refEl={readout} />
      <UPlot opts={opts} data={data} height={plotH} />
    </div>
  );
}

