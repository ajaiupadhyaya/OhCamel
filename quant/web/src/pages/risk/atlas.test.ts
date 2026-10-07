import { describe, expect, it } from "vitest";
import { atlasBooks, atlasRows, eulerRows } from "./atlas";

const rec = (book: string, method: string, alpha: number, horizon: number, v: number, es: number) => ({ book, method, alpha, horizon, var: v, es, n_paths: 1_000_000 });

describe("atlasRows (risk.mc_atlas summary table)", () => {
  it("keeps rows with the required columns and numbers", () => {
    const rows = atlasRows([rec("core", "fhs", 0.99, 1, 0.02, 0.025), { book: "x", method: "fhs" }]);
    expect(rows).toHaveLength(1);
    expect(rows![0]).toMatchObject({ book: "core", method: "fhs", alpha: 0.99, horizon: 1, var: 0.02, es: 0.025 });
  });
  it("accepts horizon_days and alpha in percent", () => {
    const rows = atlasRows([{ book: "core", method: "t_copula", alpha: 99, horizon_days: 10, var: 0.05, es: 0.06 }]);
    expect(rows![0]).toMatchObject({ alpha: 0.99, horizon: 10, method: "t_copula" });
  });
  it("returns null when the table has an unknown shape", () => {
    expect(atlasRows([{ foo: 1 }])).toBeNull();
    expect(atlasRows(null)).toBeNull();
  });
});

describe("atlasBooks", () => {
  const rows = atlasRows([
    rec("core", "fhs", 0.99, 1, 0.02, 0.025),
    rec("core", "t_copula", 0.99, 1, 0.024, 0.03),
    rec("core", "fhs", 0.95, 1, 0.012, 0.018),
    rec("spy", "fhs", 0.99, 1, 0.03, 0.04),
  ])!;
  it("pivots FHS and t-copula side by side for one alpha and horizon", () => {
    const books = atlasBooks(rows, 0.99, 1);
    expect(books.map((b) => b.book)).toEqual(["core", "spy"]);
    expect(books[0].fhs?.var).toBe(0.02);
    expect(books[0].tcop?.es).toBe(0.03);
    expect(books[0].gap).toBeCloseTo(0.004);
    expect(books[1].tcop).toBeNull();
    expect(books[1].gap).toBeNull();
  });
  it("is empty for a combination that did not run", () => {
    expect(atlasBooks(rows, 0.95, 10)).toEqual([]);
  });
});

describe("eulerRows (risk.mc_atlas euler table)", () => {
  it("reads the ES share under any of its accepted names and filters by book", () => {
    const rows = eulerRows(
      [
        { book: "core", ticker: "SPY", weight: 0.4, pct_es: 0.6 },
        { book: "core", ticker: "TLT", weight: 0.6, es_share: 0.4 },
        { book: "spy", ticker: "SPY", weight: 1, share: 1 },
      ],
      "core",
    );
    expect(rows).toEqual([
      { ticker: "SPY", weight: 0.4, share: 0.6 },
      { ticker: "TLT", weight: 0.6, share: 0.4 },
    ]);
  });
  it("returns null on an unknown shape", () => expect(eulerRows([{ a: 1 }], "core")).toBeNull());
});
