/**
 * A hierarchical-clustering tree from scipy's plot-ready coordinates (icoord / dcoord / ivl):
 * leaves at x = 5, 15, 25, …, y the merge distance. Clusters are the maximal subtrees whose
 * merge sits below the cut (70% of the tallest merge, scipy's default colour threshold).
 */
export interface DendroInput {
  icoord: number[][];
  dcoord: number[][];
  ivl: string[];
}

export interface Seg {
  ic: number[];
  dc: number[];
  top: number;
  mid: number;
}

export interface DendroModel {
  segs: Seg[];
  leafX: number[];
  members: (s: Seg) => string[];
  maxH: number;
  cut: number;
  clusterOf: Map<Seg, number>;
  leafCluster: Map<string, number>;
  nClusters: number;
}

const EPS = 1e-9;

export function dendroModel(d: DendroInput, cutShare = 0.7): DendroModel {
  const segs: Seg[] = d.icoord.map((ic, i) => ({ ic, dc: d.dcoord[i], top: d.dcoord[i][1], mid: (ic[1] + ic[2]) / 2 }));
  const leafX = d.ivl.map((_, i) => 5 + 10 * i);
  const find = (x: number, h: number) => segs.find((s) => Math.abs(s.mid - x) < 1e-6 && Math.abs(s.top - h) < EPS);
  const cache = new Map<Seg, [number, number]>();
  const span = (x: number, h: number): [number, number] => {
    if (h < EPS) return [x, x];
    const s = find(x, h);
    if (!s) return [x, x];
    const hit = cache.get(s);
    if (hit) return hit;
    const out: [number, number] = [span(s.ic[0], s.dc[0])[0], span(s.ic[3], s.dc[3])[1]];
    cache.set(s, out);
    return out;
  };
  const spanOf = (s: Seg) => span(s.mid, s.top);
  const members = (s: Seg) => {
    const [a, b] = spanOf(s);
    return d.ivl.filter((_, i) => leafX[i] >= a - EPS && leafX[i] <= b + EPS);
  };
  const maxH = Math.max(EPS, ...segs.map((s) => s.top));
  const cut = cutShare * maxH;
  const below = segs.filter((s) => s.top < cut);
  const roots = below
    .filter((s) => {
      const [a, b] = spanOf(s);
      return !below.some((o) => o !== s && o.top > s.top && spanOf(o)[0] <= a + EPS && spanOf(o)[1] >= b - EPS);
    })
    .sort((p, q) => spanOf(p)[0] - spanOf(q)[0]);
  const clusterOf = new Map<Seg, number>();
  const leafCluster = new Map<string, number>();
  roots.forEach((r, ci) => {
    const [a, b] = spanOf(r);
    for (const s of below) {
      const [sa, sb] = spanOf(s);
      if (sa >= a - EPS && sb <= b + EPS) clusterOf.set(s, ci);
    }
    for (const m of members(r)) leafCluster.set(m, ci);
  });
  return { segs, leafX, members, maxH, cut, clusterOf, leafCluster, nClusters: roots.length };
}
