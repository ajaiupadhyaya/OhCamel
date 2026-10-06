import { describe, expect, it } from "vitest";
import { tileColor } from "./SectorHeatmap";

const share = (s: string) => Number(s.match(/ (\d+)%/)?.[1]);

describe("sector tile colour", () => {
  it("keeps ink text legible: the ink/signal mix never passes 32% (4.5:1 in both themes)", () => {
    expect(share(tileColor(0.5, 0.02))).toBeLessThanOrEqual(32);
    expect(share(tileColor(-0.5, 0.02))).toBeLessThanOrEqual(32);
  });
  it("gains tint with ink and losses with signal: never green", () => {
    expect(tileColor(0.01, 0.02)).toContain("var(--ink)");
    expect(tileColor(-0.01, 0.02)).toContain("var(--signal)");
  });
  it("still grades with the move", () => expect(share(tileColor(0.02, 0.02))).toBeGreaterThan(share(tileColor(0.005, 0.02))));
  it("missing is the flat ground, never a colour", () => expect(tileColor(null, 0.02)).toBe("var(--paper-2)"));
});
