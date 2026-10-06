import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Sparkline } from "./Sparkline";

describe("Sparkline (spec 3.1/3.5: no gradients, area fills are hatched)", () => {
  const html = renderToStaticMarkup(<Sparkline values={[1, 3, 2, 5, 4]} />);
  it("draws no gradient", () => expect(html).not.toMatch(/gradient/i));
  it("fills the area with a hatch pattern", () => {
    expect(html).toMatch(/<pattern[^>]*patternUnits="userSpaceOnUse"/);
    expect(html).toMatch(/fill="url\(#[^)]+\)"/);
  });
  it("draws no area when area is false", () => {
    const flat = renderToStaticMarkup(<Sparkline values={[1, 2, 3]} area={false} />);
    expect(flat).not.toMatch(/<pattern/);
  });
});
