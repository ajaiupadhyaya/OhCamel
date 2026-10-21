import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Verdict, verdictLabel } from "./Verdict";

describe("verdictLabel (a verdict detail is a terse label)", () => {
  it("an earlier artifact's operator instruction is cut to its label", () => {
    expect(verdictLabel("universe not frozen: run `python -m ohcamel_quant.products.q01 freeze` after the warehouse backfill and commit config.yaml")).toBe("UNIVERSE NOT FROZEN");
  });
  it("clauses become ruled separators, in caps", () => {
    expect(verdictLabel("0 PASS · 0 FAIL · 21 skipped; advisory, nothing is live")).toBe("0 PASS · 0 FAIL · 21 SKIPPED · ADVISORY, NOTHING IS LIVE");
  });
  it("a label passes through", () => expect(verdictLabel("RISK MEASUREMENT · NO GATE")).toBe("RISK MEASUREMENT · NO GATE"));
  it("the stamp renders the label", () => {
    const html = renderToStaticMarkup(<Verdict value="INSUFFICIENT DATA" detail="universe not frozen: run `python -m x freeze`" />);
    expect(html).toContain("UNIVERSE NOT FROZEN");
    expect(html).not.toContain("python");
  });
});

