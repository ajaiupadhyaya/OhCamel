import { describe, expect, it } from "vitest";
import { riskSeed } from "./seed";

const q = (s: string) => new URLSearchParams(s);

describe("riskSeed (/risk?alpha=&h=)", () => {
  it("defaults to 99% over one day", () => expect(riskSeed(q(""))).toEqual({ alpha: "0.99", h: "1" }));
  it("reads the command line's link", () => expect(riskSeed(q("alpha=0.95&h=10"))).toEqual({ alpha: "0.95", h: "10" }));
  it("snaps to the nearest level the controls offer", () => {
    expect(riskSeed(q("alpha=0.975&h=5")).alpha).toBe("0.99");
    expect(riskSeed(q("alpha=0.9&h=2"))).toEqual({ alpha: "0.95", h: "1" });
    expect(riskSeed(q("alpha=0.995&h=20"))).toEqual({ alpha: "0.99", h: "10" });
  });
  it("accepts percent spellings", () => expect(riskSeed(q("alpha=95")).alpha).toBe("0.95"));
  it("ignores garbage", () => expect(riskSeed(q("alpha=abc&h=-3"))).toEqual({ alpha: "0.99", h: "1" }));
});
