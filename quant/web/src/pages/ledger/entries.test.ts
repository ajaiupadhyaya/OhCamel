import { describe, expect, it } from "vitest";
import { KINDS } from "../../lib/artifacts";
import { ROUTES } from "../../lib/routes";
import { matchPath } from "react-router-dom";
import { LEDGER, LEDGER_AS_OF, SECTION_ORDER } from "./entries";

describe("Ledger entries", () => {
  // Products P1-P7, and the three WATCH kinds the worker runs on its own (2026-10-19).
  const PRODUCT = /^(P[1-7]|WATCH) /;
  it("ties each product P1-P7 and each WATCH kind to the job kind whose latest artifact the row reads (no claim ahead of the artifacts)", () => {
    const products = LEDGER.filter((e) => PRODUCT.test(e.item));
    expect(products).toHaveLength(10);
    const kinds = products.map((e) => e.kind);
    expect(new Set(kinds)).toEqual(new Set(Object.values(KINDS)));
  });
  it("gives no other row a kind", () => {
    for (const e of LEDGER.filter((e) => !PRODUCT.test(e.item))) expect(e.kind, e.item).toBeUndefined();
  });
  it("links each product, and every row that has one, to a page that exists", () => {
    for (const e of LEDGER.filter((e) => e.kind)) expect(e.to, e.item).toBeDefined();
    for (const e of LEDGER.filter((e) => e.to)) {
      const path = (e.to ?? "").split("?")[0];
      expect(ROUTES.some((r) => matchPath({ path: r.path, end: true }, path)) || path === "/company", e.item).toBe(true);
    }
  });
});

describe("Ledger accuracy (re-dated 2026-10-08, Ship plan Lane H H2)", () => {
  it("is dated 2026-10-08", () => {
    expect(LEDGER_AS_OF).toBe("2026-10-08");
  });
  it("counts ten kernels", () => {
    expect(LEDGER.find((e) => e.item === "KERNELS")?.detail).toMatch(/^Ten Rust kernels/);
  });
  it("lists every cut of the Ship spec §2", () => {
    const cut = LEDGER.filter((e) => e.section === "CUT").map((e) => e.item);
    for (const item of ["ENGINE DEPTH", "P8 SCENARIO GRID", "C STUBS", "KIOSK MODE", "SAVED LAYOUTS", "DENSITY MODES"]) expect(cut).toContain(item);
  });
  it("names the owner's outstanding root step for backups and the live host", () => {
    const owner = LEDGER.filter((e) => e.section === "OWNER").map((e) => `${e.item} ${e.detail}`).join("\n");
    expect(owner).toMatch(/\/var\/backups\/ohcamel/);
  });
  it("claims no public-host build: it ships with that deploy, and the footer's BUILD shows this host's", () => {
    for (const e of LEDGER) expect(`${e.item} ${e.detail}`, e.item).not.toMatch(/public host/i);
    const live = LEDGER.find((e) => e.item === "LIVE HOST BEHIND")?.detail ?? "";
    expect(live).toMatch(/88ebc96/);
    expect(live).toMatch(/\/var\/backups\/ohcamel/);
    expect(live.match(/\b[0-9a-f]{7}\b/g)).toEqual(["88ebc96"]);
  });
  it("has one row per item, and every section non-empty", () => {
    const items = LEDGER.map((e) => `${e.section}:${e.item}`);
    expect(new Set(items).size).toBe(items.length);
    for (const s of SECTION_ORDER) expect(LEDGER.some((e) => e.section === s), s).toBe(true);
  });
  it("writes labels, not prose: no row detail ends in a question", () => {
    for (const e of LEDGER) expect(e.detail.trim().endsWith("?"), e.item).toBe(false);
  });
});
