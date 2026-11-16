import { describe, expect, it } from "vitest";
import { KINDS } from "../../lib/artifacts";
import { KERNEL_API } from "../methodology/compute";
import { MODELS } from "../methodology/models";
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
  it("points each product at its Methodology entry", () => {
    const ids = new Set(MODELS.map((m) => m.id));
    for (const e of LEDGER.filter((e) => e.kind)) expect(ids.has(e.methodology ?? ""), e.item).toBe(true);
  });
});

describe("Ledger accuracy (re-dated 2026-11-05)", () => {
  it("is dated 2026-11-05 and does not pin a deploy", () => {
    expect(LEDGER_AS_OF).toBe("2026-11-05");
    for (const e of LEDGER) expect(`${e.item} ${e.detail}`).not.toMatch(/\b[0-9a-f]{7}\b/);
    expect(LEDGER.map((e) => e.item)).not.toContain("BACKUPS PAUSED");
    expect(LEDGER.map((e) => e.item)).not.toContain("LIVE HOST BEHIND");
  });
  it("says incubation is sized on the paper book and the warehouse file is not copied", () => {
    const text = LEDGER.map((e) => `${e.section} ${e.item} ${e.detail}`).join("\n");
    expect(text).toMatch(/twenty-three strategies are sized as incubation/i);
    expect(text).not.toMatch(/No strategy on this site trades/);
    expect(text).toMatch(/option snapshots/);
    expect(text).toMatch(/\$700,000/);
    expect(text).toMatch(/\$30,000/);
    expect(text).toMatch(/10%/);
  });
  it("counts the kernels the Methodology documents", () => {
    expect(KERNEL_API).toHaveLength(12);
    expect(LEDGER.find((e) => e.item === "KERNELS")?.detail).toMatch(/^Twelve Rust kernels/);
  });
  it("lists every cut of the Ship spec §2", () => {
    const cut = LEDGER.filter((e) => e.section === "CUT").map((e) => e.item);
    for (const item of ["ENGINE DEPTH", "P8 SCENARIO GRID", "C STUBS", "KIOSK MODE", "SAVED LAYOUTS", "DENSITY MODES"]) expect(cut).toContain(item);
  });
  it("claims no public-host build: the footer's BUILD shows this host's", () => {
    for (const e of LEDGER) expect(`${e.item} ${e.detail}`, e.item).not.toMatch(/public host/i);
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
