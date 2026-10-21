import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { DataUnavailableError } from "../../lib/api";
import { COMPUTES } from "../Company";
import { LiveOnly, ZoneTrack } from "./shared";

describe("LiveOnly (fundamentals without SEC EDGAR)", () => {
  const err = new DataUnavailableError("offline mode: SEC filings are fetched online only", "/api/fundamentals/AAPL/profile");
  const html = renderToStaticMarkup(
    <MemoryRouter>
      <LiveOnly title="FUNDAMENTALS · AAPL" error={err} items={COMPUTES} source="/api/fundamentals/AAPL/profile" />
    </MemoryRouter>,
  );
  it("stamps INSUFFICIENT DATA with the server's reason, never a number", () => {
    expect(html).toContain("INSUFFICIENT DATA");
    expect(html).toContain("DATA UNAVAILABLE");
    expect(html).toContain("SEC filings are fetched online only");
  });
  it("lists what the page computes, each with its methodology note", () => {
    for (const c of COMPUTES) expect(html).toContain(`/methodology#${c.note}`);
    expect(html).toContain("PIOTROSKI F · 9 TESTS");
  });
});

describe("ZoneTrack", () => {
  it("marks the breach zone in signal and says when the value is off the scale", () => {
    const html = renderToStaticMarkup(<ZoneTrack min={0} max={5} value={42} format={(v) => v.toString()} ticks={[1.81]} zones={[{ from: 0, to: 1.81, label: "DISTRESS", breach: true }, { from: 1.81, to: 5, label: "SAFE" }]} />);
    expect(html).toMatch(/co-track-zone loss[^>]*>DISTRESS/);
    expect(html).toContain("42 ABOVE SCALE");
  });
});
