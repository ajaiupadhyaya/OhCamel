import { describe, expect, it } from "vitest";
import { NAV_CODES, ROUTES, navRoutes } from "./routes";

describe("route function codes", () => {
  it("gives every route a unique upper-case code and no icon", () => {
    const codes = ROUTES.map((r) => r.code);
    for (const c of codes) expect(c).toMatch(/^[A-Z]{2,5}$/);
    expect(new Set(codes).size).toBe(codes.length);
    for (const r of ROUTES) expect(r).not.toHaveProperty("icon");
  });
  it("orders the nav row by NAV_CODES, unknown codes last in array order", () => {
    expect(navRoutes().map((r) => r.code)).toEqual(["MKTS", "PORT", "OPT", "RSCH", "VOL", "RATES", "CO", "DECK", "ENG", "DOCS"]);
    expect(NAV_CODES[0]).toBe("FRONT");
  });
  it("keeps non-nav routes out of the row", () => {
    expect(navRoutes().some((r) => r.code === "GP")).toBe(false);
  });
});
