import { describe, expect, it } from "vitest";
import { KINDS } from "../../lib/artifacts";
import { KERNEL_API } from "../methodology/compute";
import { MODELS } from "../methodology/models";
import { LEDGER, LEDGER_AS_OF, SECTION_ORDER } from "./entries";

describe("Ledger entries", () => {
  it("ties each product P1-P7 to the job kind whose latest artifact the row reads (no claim ahead of the artifacts)", () => {
    const products = LEDGER.filter((e) => /^P[1-7] /.test(e.item));
    expect(products).toHaveLength(7);
    const kinds = products.map((e) => e.kind);
    expect(new Set(kinds)).toEqual(new Set(Object.values(KINDS)));
  });
  it("gives no other row a kind", () => {
    for (const e of LEDGER.filter((e) => !/^P[1-7] /.test(e.item))) expect(e.kind, e.item).toBeUndefined();
  });
  it("points each product at its Methodology entry", () => {
    const ids = new Set(MODELS.map((m) => m.id));
    for (const e of LEDGER.filter((e) => e.kind)) expect(ids.has(e.methodology ?? ""), e.item).toBe(true);
  });
});

describe("Ledger accuracy (re-dated 2026-10-08, Ship plan Lane H H2)", () => {
  it("is dated 2026-10-08", () => {
    expect(LEDGER_AS_OF).toBe("2026-10-08");
  });
  it("counts the kernels the Methodology documents", () => {
    expect(KERNEL_API).toHaveLength(10);
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
  it("has one row per item, and every section non-empty", () => {
    const items = LEDGER.map((e) => `${e.section}:${e.item}`);
    expect(new Set(items).size).toBe(items.length);
    for (const s of SECTION_ORDER) expect(LEDGER.some((e) => e.section === s), s).toBe(true);
  });
  it("writes labels, not prose: no row detail ends in a question", () => {
    for (const e of LEDGER) expect(e.detail.trim().endsWith("?"), e.item).toBe(false);
  });
});
