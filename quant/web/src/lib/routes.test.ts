import { describe, expect, it } from "vitest";
import { matchPath } from "react-router-dom";
import { NAV_CODES, ROUTES, navRoutes } from "./routes";

const resolves = (url: string) => ROUTES.some((r) => matchPath({ path: r.path, end: true }, url.split("?")[0]));

describe("routes (Review Focus 4)", () => {
  it("keeps every old bookmark", () => {
    for (const u of ["/", "/deck", "/portfolio", "/optimize", "/research", "/options", "/macro", "/company/AAPL", "/ticker/SPY", "/engine", "/methodology"])
      expect(resolves(u), u).toBe(true);
  });
  it("adds the new pages", () => {
    for (const u of ["/markets", "/risk", "/system", "/compute", "/ledger"]) expect(resolves(u), u).toBe(true);
  });
  it("every nav route has a unique code in the spec's order", () => {
    const codes = ROUTES.filter((r) => r.nav).map((r) => r.code);
    expect(codes).toEqual(["FRONT", "MKTS", "RISK", "PORT", "OPT", "RSCH", "VOL", "RATES", "CO", "DECK", "SYS"]);
  });
});

describe("route function codes", () => {
  it("gives every route a unique upper-case code and no icon", () => {
    const codes = ROUTES.map((r) => r.code);
    for (const c of codes) expect(c).toMatch(/^[A-Z]{2,7}$/);
    expect(new Set(codes).size).toBe(codes.length);
    for (const r of ROUTES) expect(r).not.toHaveProperty("icon");
  });
  it("orders the nav row by NAV_CODES", () => {
    expect(navRoutes().map((r) => r.code)).toEqual([...NAV_CODES]);
  });
  it("keeps non-nav routes out of the row", () => {
    for (const c of ["GP", "COMPUTE", "ENG", "DOCS", "LEDGER"]) expect(navRoutes().some((r) => r.code === c), c).toBe(false);
  });
  it("Markets content lives at /markets and the Front Page takes /", () => {
    expect(ROUTES.find((r) => r.path === "/")?.code).toBe("FRONT");
    expect(ROUTES.find((r) => r.path === "/markets")?.code).toBe("MKTS");
  });
});
