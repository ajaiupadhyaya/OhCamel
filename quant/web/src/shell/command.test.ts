import { describe, expect, it } from "vitest";
import { commandPath, parseCommand, pushRecent } from "./command";

describe("parseCommand", () => {
  it("function codes go to pages", () => {
    expect(parseCommand("rates")).toEqual({ kind: "go", path: "/macro" });
    expect(parseCommand("GO VOL")).toEqual({ kind: "go", path: "/options" });
    expect(parseCommand("sys")).toEqual({ kind: "go", path: "/system" });
  });
  it("ticker functions", () => {
    expect(parseCommand("spy gp")).toEqual({ kind: "ticker", ticker: "SPY", fn: "GP" });
    expect(parseCommand("AAPL DES")).toEqual({ kind: "ticker", ticker: "AAPL", fn: "DES" });
    expect(parseCommand("BRK.B GP")).toEqual({ kind: "ticker", ticker: "BRK.B", fn: "GP" });
    expect(commandPath(parseCommand("AAPL DES"))).toBe("/company/AAPL");
    expect(commandPath(parseCommand("QQQ OMON"))).toBe("/options?ticker=QQQ");
  });
  it("portfolio risk with alpha and horizon", () => {
    expect(parseCommand("PORT RISK 99 10D")).toEqual({ kind: "risk", alpha: 0.99, horizon: 10 });
    expect(parseCommand("port risk")).toEqual({ kind: "risk", alpha: 0.99, horizon: 1 });
    expect(commandPath(parseCommand("PORT RISK 95 1D"))).toBe("/risk?alpha=0.95&h=1");
  });
  it("jobs", () => {
    expect(parseCommand("job farm")).toEqual({ kind: "job", job: "farm" });
    expect(commandPath(parseCommand("JOB FARM"))).toBe("/compute?kind=farm");
  });
  it("never throws on garbage (Review Focus 2)", () => {
    expect(parseCommand("   ")).toEqual({ kind: "unknown", input: "" });
    expect(parseCommand("???")).toEqual({ kind: "search", query: "???" });
    expect(parseCommand("spy gp extra")).toEqual({ kind: "search", query: "spy gp extra" });
    expect(parseCommand("PORT RISK 150 10D")).toEqual({ kind: "search", query: "PORT RISK 150 10D" });
    const long = "x".repeat(200);
    expect(parseCommand(long)).toEqual({ kind: "search", query: long.slice(0, 120) });
    expect(commandPath({ kind: "unknown", input: "" })).toBeNull();
  });
  it("a bare ticker-looking word searches, it does not navigate", () => {
    expect(parseCommand("nvda")).toEqual({ kind: "search", query: "nvda" });
  });
});

describe("pushRecent", () => {
  it("puts the newest first, upper-cased, without duplicates, at most 8", () => {
    expect(pushRecent(["SPY GP", "RATES"], "rates")).toEqual(["RATES", "SPY GP"]);
    expect(pushRecent([], "  aapl des ")).toEqual(["AAPL DES"]);
    const nine = Array.from({ length: 9 }, (_, i) => `C${i}`);
    expect(pushRecent(nine, "NEW")).toEqual(["NEW", ...nine.slice(0, 7)]);
  });
  it("ignores blanks and tolerates a corrupt stored value", () => {
    expect(pushRecent(["RATES"], "   ")).toEqual(["RATES"]);
    expect(pushRecent("garbage" as unknown as string[], "VOL")).toEqual(["VOL"]);
    expect(pushRecent([1, null, "SYS"] as unknown as string[], "VOL")).toEqual(["VOL", "SYS"]);
  });
});
