import { matchPath } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ROUTES } from "../../lib/routes";
import { ENGINE, EXTRA_SECTIONS, KERNELS, KERNEL_API } from "./compute";
import { MODELS } from "./models";
import { REFS } from "./references";

const resolves = (to: string) => ROUTES.some((r) => matchPath({ path: r.path, end: true }, to.split("?")[0]));

describe("Methodology: engine and kernel entries (Ship plan P2-7)", () => {
  it("has one entry per kernel of contract II.4, id kernel-<name> (the Compute table links there)", () => {
    for (const k of KERNEL_API) expect(KERNELS.some((e) => e.id === `kernel-${k}`), k).toBe(true);
    expect(KERNEL_API).toHaveLength(10);
  });
  it("has the engine entry the Engine page's Note points to", () => {
    expect(ENGINE.map((e) => e.id)).toContain("engine-incremental");
  });
  it("cites only references that exist, and links only to routes that resolve", () => {
    for (const s of EXTRA_SECTIONS)
      for (const e of s.entries) {
        for (const r of e.refs) expect(REFS[r], `${e.id} cites ${r}`).toBeDefined();
        for (const a of e.appears) expect(resolves(a.to), `${e.id} → ${a.to}`).toBe(true);
      }
  });
  it("uses ids that collide neither with each other nor with the model entries", () => {
    const ids = [...MODELS.map((m) => m.id), ...EXTRA_SECTIONS.flatMap((s) => s.entries.map((e) => e.id))];
    expect(new Set(ids).size).toBe(ids.length);
  });
});
