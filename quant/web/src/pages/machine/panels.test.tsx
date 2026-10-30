import { renderToStaticMarkup } from "react-dom/server";
import { createRef } from "react";
import { describe, expect, it } from "vitest";
import { FIXTURE, FIXTURE_BROKEN, FIXTURE_NO_SUMMARY } from "./fixture";
import { FocusPanel } from "./FocusPanel";
import { SidePanel, type SidePanelProps } from "./SidePanel";
import { STEPS } from "./tour";
import type { MachineOut } from "./types";
import type { Tab } from "./url";
import { indexGraph, type WebMode } from "./web";

const NOW = new Date(FIXTURE.asof);
const quiet = () => {};

function focus(out: MachineOut, id: string, mode: WebMode = "web"): string {
  const gi = indexGraph(out);
  return renderToStaticMarkup(<FocusPanel gi={gi} i={gi.byId.get(id)!} mode={mode} runs={out.runs} now={NOW} onSelect={quiet} onClose={quiet} />);
}

function side(out: MachineOut, tab: Tab, over: Partial<SidePanelProps["view"]> = {}, held: string | null = null): string {
  return renderToStaticMarkup(
    <SidePanel
      gi={indexGraph(out)}
      view={{ sel: null, tab, step: 1, ...over }}
      runs={out.runs}
      now={NOW}
      replay={{ playing: false, held, clock: createRef<HTMLSpanElement>() }}
      onTab={quiet}
      onStep={quiet}
      onSelect={quiet}
      onReplay={quiet}
      onStopReplay={quiet}
    />,
  );
}

const CLEAN = /NaN|undefined|\[object|null/;
const VARIANTS: [string, MachineOut][] = [["full", FIXTURE], ["no summary", FIXTURE_NO_SUMMARY], ["lens unreadable", FIXTURE_BROKEN]];

describe("the panels, on every node of every fixture", () => {
  for (const [name, out] of VARIANTS) {
    it(`${name}: the focus panel and the reading print no empty value`, () => {
      for (const n of out.nodes) {
        expect(focus(out, n.id), n.id).not.toMatch(CLEAN);
        expect(side(out, "read", { sel: n.id }), n.id).not.toMatch(CLEAN);
      }
    });

    it(`${name}: every tour step and the day print no empty value`, () => {
      STEPS.forEach((_, k) => expect(side(out, "tour", { step: k + 1 }), `step ${k + 1}`).not.toMatch(CLEAN));
      expect(side(out, "day")).not.toMatch(CLEAN);
    });
  }
});

describe("FocusPanel", () => {
  it("gives the product its provenance, and only what it has", () => {
    const farm = focus(FIXTURE, "p:farm.sweep");
    expect(farm).toContain("mx-prov");
    expect(farm).toContain("0dfae1f");
    expect(farm).toContain("python");
    const desk = focus(FIXTURE, "p:desk.report");
    expect(desk).toContain("finished");
    expect(desk).not.toContain("cpu-s");
  });

  it("shows no provenance row for what is not a product", () => {
    expect(focus(FIXTURE, "feed:prices")).not.toContain("mx-prov");
  });

  it("stamps a verdict by its word", () => {
    expect(focus(FIXTURE, "c:xsmom@sectors")).toContain("mx-verdict fail");
    expect(focus(FIXTURE, "c:tsmom@us_equity_indices")).toContain("mx-verdict pass");
    expect(focus(FIXTURE, "p:vol.forecast_league")).toContain("mx-verdict desc");
  });

  it("says a missing node is missing, with the reason, and draws no lens", () => {
    const out = structuredClone(FIXTURE) as MachineOut;
    const farm = out.nodes.find((n) => n.id === "p:farm.sweep")!;
    farm.status = "missing";
    farm.why = "no farm.sweep artifact has been published";
    const html = focus(out, "p:farm.sweep");
    expect(html).toContain("MISSING");
    expect(html).toContain(farm.why);
    expect(html).not.toContain("leaderboard");
  });

  it("marks an artifact past its schedule's max age", () => {
    const out = structuredClone(FIXTURE) as MachineOut;
    out.nodes.find((n) => n.id === "p:farm.sweep")!.m.stale = true;
    expect(focus(out, "p:farm.sweep")).toContain("STALE");
  });

  it("calls a return one year only on a year of closes", () => {
    const out = structuredClone(FIXTURE) as MachineOut;
    const nvda = out.nodes.find((n) => n.id === "d:NVDA")!;
    Object.assign(nvda.m, { n: 253, ret1y: 0.5, last: 100, vol: 0.4 });
    expect(focus(out, "d:NVDA")).toContain("one year");
    nvda.m.n = 40;
    const short = focus(out, "d:NVDA");
    expect(short).not.toContain("one year");
    expect(short).toContain("40 sessions");
  });

  it("follows the web mode: no web, no reads or feeds", () => {
    expect(focus(FIXTURE, "p:farm.sweep", "web")).toMatch(/reads.*feeds/s);
    expect(focus(FIXTURE, "p:farm.sweep", "reads")).not.toContain(">feeds");
    expect(focus(FIXTURE, "p:farm.sweep", "none")).not.toContain("mx-chips");
  });

  it("writes the farm's leaderboard, the leagues, the atlas and the deciles from their lenses", () => {
    /** The fixture's league node as another product, whose lens the fixture does not hold. */
    const lensOf = (kind: string, lens: unknown): string => {
      const out = structuredClone(FIXTURE) as MachineOut;
      const n = out.nodes.find((x) => x.id === "p:vol.forecast_league")!;
      n.m.kind = kind;
      n.m.lens = lens;
      return focus(out, n.id);
    };
    expect(focus(FIXTURE, "p:farm.sweep")).toContain("leaderboard");
    expect(focus(FIXTURE, "p:vol.forecast_league")).toContain("61%");
    const cov = lensOf("cov.league", { estimators: [["oas", "ndx_100", 0.1234, 2], ["oas", "sectors", 0.0987, 1], ["sample", "ndx_100", 0.14, 3]] });
    expect(cov).toContain("ndx 100");
    expect(cov).toContain("12.3%");
    expect(cov).toContain("#2");
    const atlas = lensOf("risk.mc_atlas", { measures: [["fhs", 10, -0.0345, -0.05, 200000]] });
    expect(atlas).toContain("VaR");
    expect(atlas).toContain("FHS");
    expect(atlas).toContain("−3.45%");
    const xs = lensOf("models.xs_lgbm", { deciles: [[2, 0.01], [1, -0.01]] });
    expect(xs.match(/<rect/g)).toHaveLength(2);
    expect(xs).toContain('class="neg"');
    expect(xs.indexOf("Decile 1")).toBeLessThan(xs.indexOf("Decile 2"));
  });

  it("draws no lens from a table it cannot read", () => {
    const out = structuredClone(FIXTURE) as MachineOut;
    const n = out.nodes.find((x) => x.id === "p:vol.forecast_league")!;
    for (const [kind, lens] of [
      ["vol.forecast_league", { models: "nope" }], ["cov.league", { estimators: [[1, 2]] }], ["risk.mc_atlas", {}], ["models.xs_lgbm", { deciles: [["x", null]] }], ["farm.sweep", 7],
    ] as const) {
      n.m.kind = kind;
      n.m.lens = lens;
      expect(focus(out, n.id), kind).not.toContain("mx-kv");
      expect(focus(out, n.id), kind).not.toContain("<rect");
    }
  });
});

describe("SidePanel", () => {
  it("asks for a selection when the Reading tab has none", () => {
    expect(side(FIXTURE, "read")).toContain("Click anything");
  });

  it("lists a reading's caveats", () => {
    expect(side(FIXTURE, "read", { sel: "p:farm.sweep" })).toContain("mx-notes");
  });

  it("disables back on the first step and next on the last", () => {
    expect(side(FIXTURE, "tour", { step: 1 })).toMatch(/<button[^>]*disabled[^>]*>↑ back/);
    expect(side(FIXTURE, "tour", { step: STEPS.length })).toMatch(/<button[^>]*disabled[^>]*>next ↓/);
  });

  it("marks the tour's bodies and the reading polite for a screen reader", () => {
    expect(side(FIXTURE, "tour")).toContain('aria-live="polite"');
    expect(side(FIXTURE, "read", { sel: "book" })).toContain('aria-live="polite"');
  });

  it("puts a running job first, with its progress", () => {
    const day = side(FIXTURE, "day");
    expect(day.indexOf("mx-run  running") < 0 ? day.indexOf("running") : day.indexOf("mx-run  running")).toBeLessThan(day.indexOf("Daily bars"));
    expect(day).toContain("70%");
    expect(day).toContain("--p:0.7");
  });

  it("lights the held run's row", () => {
    const held = FIXTURE.runs[0].id;
    expect(side(FIXTURE, "day", {}, held)).toMatch(/class="mx-run on/);
    expect(side(FIXTURE, "day")).not.toMatch(/class="mx-run on/);
  });

  it("will not replay an empty day", () => {
    const out = { ...FIXTURE, runs: [] };
    expect(side(out, "day")).toMatch(/<button[^>]*disabled[^>]*>▶ replay the day/);
    expect(side(out, "day")).toContain("No runs in the last day.");
  });

  it("leaves a run of a job that is not in the graph unclickable", () => {
    const out = { ...FIXTURE, runs: [{ ...FIXTURE.runs[0], kind: "gone.job" }] };
    expect(side(out, "day")).toMatch(/<button[^>]*disabled[^>]*>.*gone\.job/s);
  });
});

describe("text from the wire", () => {
  it("is escaped wherever a panel prints it", () => {
    const evil = "<img src=x onerror=alert(1)>";
    const out = structuredClone(FIXTURE) as MachineOut;
    for (const n of out.nodes) {
      n.n = evil;
      n.l = evil;
      if (typeof n.m.detail === "string") n.m.detail = evil;
      if (typeof n.m.verdict === "string") n.m.verdict = evil;
      if (Array.isArray(n.m.notes)) n.m.notes = [evil];
    }
    out.runs = out.runs.map((r) => ({ ...r, msg: evil }));
    for (const id of ["p:farm.sweep", "d:NVDA", "j:farm.sweep", "feed:prices", "book"]) {
      expect(focus(out, id), id).not.toContain("<img");
      expect(side(out, "read", { sel: id }), id).not.toContain("<img");
    }
    expect(side(out, "day")).not.toContain("<img");
    STEPS.forEach((_, k) => expect(side(out, "tour", { step: k + 1 })).not.toContain("<img"));
  });
});
