import { describe, expect, it } from "vitest";
import { bridgeRows } from "./DcfTab";
import { ratioSeries } from "./RatiosTab";
import { fmtCell } from "./StatementsTab";
import { periodLabel, trackPos } from "./shared";
import type { DcfOut } from "./types";

describe("periodLabel", () => {
  it("labels fiscal years and quarter ends in caps", () => {
    expect(periodLabel("2024-12-31", "annual")).toBe("FY24");
    expect(periodLabel("2024-09-28", "quarterly")).toBe("SEP 24");
    expect(periodLabel("2025-03-31", "ttm")).toBe("MAR 25");
    expect(periodLabel("bad", "annual")).toBe("bad");
  });
});

describe("trackPos", () => {
  it("places a value on the scale as a clamped percent", () => {
    expect(trackPos(2.5, 0, 5)).toBe(50);
    expect(trackPos(-1, 0, 5)).toBe(0);
    expect(trackPos(9, 0, 5)).toBe(100);
    expect(trackPos(1, 3, 3)).toBe(50);
  });
});

describe("fmtCell", () => {
  it("shows USD millions, outflows in parentheses, EPS in dollars", () => {
    expect(fmtCell(391_035e6, "revenue")).toBe("391,035");
    expect(fmtCell(210_352e6, "cost_of_revenue")).toBe("(210,352)");
    expect(fmtCell(-4_200e6, "net_income")).toBe("−4,200");
    expect(fmtCell(6.08, "eps_diluted")).toBe("6.08");
    expect(fmtCell(3.4e6, "cash")).toBe("3.4");
    expect(fmtCell(null, "revenue")).toBe("—");
  });
});

describe("ratioSeries", () => {
  it("keeps keys with data, in order, styled ink first", () => {
    const frame = { index: ["2023-12-31", "2024-12-31"], columns: [], data: { gross_margin: [0.4, 0.45], operating_margin: [null, null], net_margin: [0.1, 0.12] } };
    const s = ratioSeries(frame, ["gross_margin", "operating_margin", "net_margin"]);
    expect(s.map((x) => x.name)).toEqual(["GROSS", "NET"]);
    expect(s[0]).toMatchObject({ tone: "ink", dash: "solid" });
    expect(s[1]).toMatchObject({ tone: "ink", dash: "dash" });
  });
});

describe("bridgeRows", () => {
  it("runs from the yearly PVs through EV and net debt to equity", () => {
    const d = { valuation: { table: [{ year: 1, pv: 10 }, { year: 2, pv: 9 }], pv_terminal: 81, enterprise_value: 100, net_debt: 20, equity_value: 80 } } as unknown as DcfOut;
    const r = bridgeRows(d);
    expect(r.map((x) => x.k)).toEqual(["Y1", "Y2", "TERMINAL", "EV", "− NET DEBT", "EQUITY"]);
    expect(r.find((x) => x.k === "− NET DEBT")!.v).toBe(-20);
    expect(r.filter((x) => x.total).map((x) => x.k)).toEqual(["EV", "EQUITY"]);
  });
});
