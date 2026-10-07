import { describe, expect, it } from "vitest";
import { dendroModel } from "./dendro";

// scipy-style plot coordinates: A,B merge at 0.2, then {A,B} with C at 1.0
const D = {
  icoord: [
    [5, 5, 15, 15],
    [10, 10, 25, 25],
  ],
  dcoord: [
    [0, 0.2, 0.2, 0],
    [0.2, 1.0, 1.0, 0],
  ],
  ivl: ["A", "B", "C"],
};

describe("dendroModel", () => {
  it("leaves sit at 5, 15, 25 and the cut is 70% of the tallest merge", () => {
    const m = dendroModel(D);
    expect(m.leafX).toEqual([5, 15, 25]);
    expect(m.maxH).toBe(1);
    expect(m.cut).toBeCloseTo(0.7);
  });
  it("a merge's members are the leaves under it", () => {
    const m = dendroModel(D);
    expect(m.members(m.segs[0])).toEqual(["A", "B"]);
    expect(m.members(m.segs[1])).toEqual(["A", "B", "C"]);
  });
  it("clusters are the maximal subtrees below the cut", () => {
    const m = dendroModel(D);
    expect(m.nClusters).toBe(1);
    expect(m.leafCluster.get("A")).toBe(0);
    expect(m.leafCluster.get("B")).toBe(0);
    expect(m.leafCluster.get("C")).toBeUndefined();
    expect(m.clusterOf.get(m.segs[0])).toBe(0);
    expect(m.clusterOf.get(m.segs[1])).toBeUndefined();
  });
});
