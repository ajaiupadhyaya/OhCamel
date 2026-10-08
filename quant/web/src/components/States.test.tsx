import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DataUnavailableError } from "../lib/api";
import { ErrorState } from "./States";

describe("ErrorState (a vendor error reads as a label, the sentence stays in the title)", () => {
  it("DATA UNAVAILABLE with a reason code and a series count, no Python list on the page", () => {
    const html = renderToStaticMarkup(<ErrorState error={new DataUnavailableError("offline mode: FRED series not in fixtures: ['DGS1MO', 'DGS3MO']", "/api/macro/curve")} />);
    expect(html).toContain("DATA UNAVAILABLE");
    expect(html).toContain(">OFFLINE · NO FIXTURE · 2 SERIES<");
    expect(html).toMatch(/title="offline mode: FRED series not in fixtures/);
  });
  it("an unknown reason stays verbatim", () => {
    const html = renderToStaticMarkup(<ErrorState error={new DataUnavailableError("hostd_unavailable", "/api/ops/host")} />);
    expect(html).toContain(">hostd_unavailable<");
  });
});
