import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ArtifactCell } from "../components/ArtifactCell";
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

describe("ArtifactCell note", () => {
  it("a cell can footnote its methodology entry beside the title", () => {
    const html = renderToStaticMarkup(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <ArtifactCell title="MODELS" kind="models.xs_lgbm" note={{ n: 1, to: "p5-exp-q01" }} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(html).toContain('href="/methodology#p5-exp-q01"');
  });
});
