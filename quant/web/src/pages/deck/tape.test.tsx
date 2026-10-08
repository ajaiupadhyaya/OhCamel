import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { TapeColumns } from "./model";
import { SessionTape, TAPE_EMPTY } from "./SessionTape";

const tape: TapeColumns = { ts: ["2026-09-24T14:00:00Z", "2026-09-24T14:01:00Z", "2026-09-24T14:10:00Z"], day_pnl_usd: [10, 20, 30], var_usd: [100, 100, 100], es_usd: [150, 150, 150], utilisation: { cap: [0.9, 1.1, 1.2] }, pnlLabel: "Day P&L" };

describe("SessionTape copy is labels, not sentences", () => {
  it("the readout says LATEST, not an instruction", () => {
    const html = renderToStaticMarkup(<SessionTape tape={tape} emptyText={TAPE_EMPTY.trail} />);
    expect(html).toContain("LATEST");
    expect(html).not.toMatch(/Tape inspection only|retain the current reading/);
  });
  it("every empty-tape reason is a caps label", () => {
    for (const t of Object.values(TAPE_EMPTY)) {
      expect(t).toBe(t.toUpperCase());
      expect(t).not.toMatch(/[.;:—]/);
    }
  });
});
