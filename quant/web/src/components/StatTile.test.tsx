import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StatTile } from "./StatTile";

describe("StatTile delta colour (signal only for losses)", () => {
  it("a fall is a loss by default", () => expect(renderToStaticMarkup(<StatTile label="SPY" value="1" delta={-0.01} />)).toMatch(/oc-stat-delta num loss/));
  it("a level that is neither gain nor loss (VIX) is never signal, either way", () => {
    for (const d of [0.0477, -0.05]) {
      const html = renderToStaticMarkup(<StatTile label="VIX" value="16" delta={d} neutralDelta />);
      expect(html).not.toMatch(/\bloss\b/);
      expect(html).toContain(d > 0 ? "+4.77%" : "−5.00%");
    }
  });
});
