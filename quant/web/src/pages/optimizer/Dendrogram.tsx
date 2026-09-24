/**
 * HRP / HERC dendrogram drawn from the linkage's plot-ready coordinates (scipy icoord/dcoord)
 * as Plotly path shapes, with hoverable merge nodes. Leaves sit at x = 5, 15, 25, …; y is the merge distance. Links below a cut
 * (70 % of the tallest merge, scipy's default) take the colour of their cluster.
 */
import { useMemo } from "react";
import type { Data } from "plotly.js";
import { Chart } from "../../components";
import { fmtNum, fmtPct } from "../../lib/format";
import type { Tokens } from "../../lib/theme";
import type { Dendrogram as Dendro } from "./types";

interface Seg {
  ic: number[];
  dc: number[];
  top: number;
  mid: number;
}

const EPS = 1e-9;
const colorFor = (t: Tokens, ci: number | undefined) =>
  ci === undefined || ci >= 8 ? t.text3 : t.categorical[ci];

export function Dendrogram({
  d,
  weights,
  height = 300,
}: {
  d: Dendro;
  weights?: Record<string, number>;
  height?: number;
}) {
  const model = useMemo(() => {
    const segs: Seg[] = d.icoord.map((ic, i) => ({
      ic,
      dc: d.dcoord[i],
      top: d.dcoord[i][1],
      mid: (ic[1] + ic[2]) / 2,
    }));
    const leafX = d.ivl.map((_, i) => 5 + 10 * i);
    const find = (x: number, h: number) =>
      segs.find((s) => Math.abs(s.mid - x) < 1e-6 && Math.abs(s.top - h) < EPS);
    const spanCache = new Map<Seg, [number, number]>();
    const span = (x: number, h: number): [number, number] => {
      if (h < EPS) return [x, x];
      const s = find(x, h);
      if (!s) return [x, x];
      const hit = spanCache.get(s);
      if (hit) return hit;
      const out: [number, number] = [
        span(s.ic[0], s.dc[0])[0],
        span(s.ic[3], s.dc[3])[1],
      ];
      spanCache.set(s, out);
      return out;
    };
    const members = (s: Seg) => {
      const [a, b] = span(s.mid, s.top);
      return d.ivl.filter((_, i) => leafX[i] >= a - EPS && leafX[i] <= b + EPS);
    };
    const maxH = Math.max(...segs.map((s) => s.top), EPS);
    const cut = 0.7 * maxH;
    // clusters = maximal subtrees whose top is below the cut
    const below = segs.filter((s) => s.top < cut);
    const roots = below.filter((s) => {
      const [a, b] = span(s.mid, s.top);
      return !below.some(
        (o) =>
          o !== s &&
          o.top > s.top &&
          span(o.mid, o.top)[0] <= a + EPS &&
          span(o.mid, o.top)[1] >= b - EPS,
      );
    });
    roots.sort((p, q) => span(p.mid, p.top)[0] - span(q.mid, q.top)[0]);
    const clusterOf = new Map<Seg, number>();
    const leafCluster = new Map<string, number>();
    roots.forEach((r, ci) => {
      const [a, b] = span(r.mid, r.top);
      for (const s of below) {
        const [sa, sb] = span(s.mid, s.top);
        if (sa >= a - EPS && sb <= b + EPS) clusterOf.set(s, ci);
      }
      for (const m of members(r)) leafCluster.set(m, ci);
    });
    return {
      segs,
      leafX,
      members,
      maxH,
      cut,
      clusterOf,
      leafCluster,
      nClusters: roots.length,
    };
  }, [d]);

  const data = useMemo(
    () =>
      (t: Tokens): Data[] => {
        const nodes = {
          type: "scatter",
          mode: "markers",
          x: model.segs.map((s) => s.mid),
          y: model.segs.map((s) => s.top),
          marker: {
            size: 7,
            color: model.segs.map((s) => colorFor(t, model.clusterOf.get(s))),
            line: { color: t.surface, width: 1.5 },
          },
          text: model.segs.map((s) => {
            const m = model.members(s);
            const w = weights
              ? m.reduce((a, k) => a + (weights[k] ?? 0), 0)
              : null;
            return `<b>${m.join(" · ")}</b><br>merge distance ${fmtNum(s.top, 3)}${w !== null ? `<br>cluster weight ${fmtPct(w, 1)}` : ""}`;
          }),
          hovertemplate: "%{text}<extra></extra>",
          showlegend: false,
        };
        return [nodes] as Data[];
      },
    [model, weights],
  );

  const layout = useMemo(
    () => (t: Tokens) => ({
      showlegend: false,
      hovermode: "closest",
      margin: { l: 44, r: 12, t: 12, b: weights ? 52 : 36 },
      xaxis: {
        tickmode: "array",
        tickvals: model.leafX,
        ticktext: d.ivl.map((k) =>
          weights
            ? `<b>${k}</b><br>${fmtPct(weights[k] ?? 0, 1)}`
            : `<b>${k}</b>`,
        ),
        tickfont: { family: t.fontMono, size: 11, color: t.text2 },
        showgrid: false,
        showline: false,
        zeroline: false,
        showspikes: false,
        range: [0, 10 * d.ivl.length],
        fixedrange: true,
      },
      yaxis: {
        title: { text: "correlation distance" },
        rangemode: "tozero",
        tickformat: ".2f",
        fixedrange: true,
        zeroline: false,
      },
      shapes: [
        ...model.segs.map((s) => ({
          type: "path",
          path: `M ${s.ic[0]},${s.dc[0]} L ${s.ic[1]},${s.dc[1]} L ${s.ic[2]},${s.dc[2]} L ${s.ic[3]},${s.dc[3]}`,
          line: { color: colorFor(t, model.clusterOf.get(s)), width: 1.6 },
          layer: "below",
        })),
        {
          type: "line",
          xref: "paper",
          x0: 0,
          x1: 1,
          y0: model.cut,
          y1: model.cut,
          line: { color: t.ruleStrong, width: 1, dash: "dot" },
        },
      ],
      annotations: [
        {
          xref: "paper",
          x: 1,
          y: model.cut,
          yanchor: "bottom",
          xanchor: "right",
          showarrow: false,
          text: `cluster cut · ${model.nClusters} clusters`,
          font: { size: 10, color: t.text3 },
        },
      ],
    }),
    [model, d.ivl, weights],
  );

  return (
    <Chart
      data={data}
      layout={layout as never}
      height={height}
      ariaLabel="Hierarchical clustering dendrogram"
    />
  );
}
