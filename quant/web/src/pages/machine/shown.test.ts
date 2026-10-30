import { describe, expect, it } from "vitest";
import { FIXTURE } from "./fixture";
import { chipLabel, shownLabel } from "./shown";
import type { MNode } from "./types";

const at = (k: MNode["k"], l: string, m: Record<string, unknown> = {}, n = ""): Pick<MNode, "k" | "l" | "n" | "m"> => ({ k, l, n, m });

describe("shownLabel", () => {
  it("writes a farm cell's key in words: the at sign is 'on', underscores are spaces", () => {
    expect(shownLabel(at("result", "dual_momentum@international"))).toBe("dual momentum on international");
    expect(shownLabel(at("result", "tsmom@us_equity_indices"))).toBe("tsmom on us equity indices");
  });

  it("reads every result of the fixture as the plain name the server gives it", () => {
    const cells = FIXTURE.nodes.filter((n) => n.k === "result" && n.l.includes("@"));
    expect(cells.length).toBeGreaterThan(1);
    for (const n of cells) expect(shownLabel(n)).toBe(n.n);
  });

  it("leaves the label of every other kind as it is, underscores and all", () => {
    for (const k of ["series", "strategy", "product", "job", "feed", "book", "kill", "limit"] as const) expect(shownLabel(at(k, "a_b@c"))).toBe("a_b@c");
  });

  it("leaves a result label that has neither mark alone", () => {
    expect(shownLabel(at("result", "GARCH"))).toBe("GARCH");
  });
});

describe("chipLabel", () => {
  it("names the track of a series that is not the ticker's daily bars", () => {
    expect(chipLabel(at("series", "AAPL", { track: "minute" }))).toBe("AAPL minute");
    expect(chipLabel(at("series", "AAPL", { track: "options" }))).toBe("AAPL options");
    expect(chipLabel(at("series", "AAPL", { track: "filings" }))).toBe("AAPL filings");
  });

  it("leaves the daily bars, and series that carry their own names, as they are", () => {
    expect(chipLabel(at("series", "AAPL", { track: "daily" }))).toBe("AAPL");
    expect(chipLabel(at("series", "CPIAUCSL", { track: "macro" }))).toBe("CPIAUCSL");
    expect(chipLabel(at("series", "FF5", { track: "factors" }))).toBe("FF5");
    expect(chipLabel(at("series", "AAPL"))).toBe("AAPL");
  });

  it("gives a result its plain words and names no track for anything but a series", () => {
    expect(chipLabel(at("result", "xsmom@sectors", { track: "minute" }))).toBe("xsmom on sectors");
    expect(chipLabel(at("strategy", "pairs distance sectors", { track: "minute" }))).toBe("pairs distance sectors");
  });

  it("gives a product its plain name, not its code", () => {
    expect(chipLabel(at("product", "SO", {}, "Exposure overlay"))).toBe("Exposure overlay");
    for (const n of FIXTURE.nodes.filter((x) => x.k === "product")) expect(chipLabel(n)).toBe(n.n);
    expect(chipLabel(at("product", "SO"))).toBe("SO");   // a nameless product keeps what it has
  });

  it("names no track for a wire value that happens to be a property of every object", () => {
    expect(chipLabel(at("series", "AAPL", { track: "constructor" }))).toBe("AAPL");
    expect(chipLabel(at("series", "AAPL", { track: "toString" }))).toBe("AAPL");
  });

  it("makes the chips of one ticker's tracks different from each other", () => {
    const ticker = FIXTURE.nodes.filter((n) => n.k === "series" && n.l === "NVDA");
    expect(ticker.length).toBeGreaterThan(1);
    expect(new Set(ticker.map(chipLabel)).size).toBe(ticker.length);
  });
});
