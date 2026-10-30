import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { FIXTURE, FIXTURE_BROKEN, FIXTURE_NO_SUMMARY } from "./fixture";
import { RADIUS } from "./layout";
import { ListView, outline } from "./ListView";
import type { MachineOut } from "./types";
import { parseView } from "./url";
import { indexGraph } from "./web";

const NOW = new Date(FIXTURE.asof);

function list(out: MachineOut, address = "?view=list", over: { drawable?: boolean; note?: string | null } = {}): string {
  const gi = indexGraph(out);
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[`/${address}`]}>
        <ListView gi={gi} view={parseView(address, (id) => gi.byId.has(id))} runs={out.runs} now={NOW} drawable note={null} onSelect={() => {}} {...over} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const CLEAN = /NaN|undefined|\[object/;
const openNodes = (html: string): number => (html.match(/<details[^>]* open=""/g) ?? []).length;

describe("outline: the nodes by ring", () => {
  const gi = indexGraph(FIXTURE);

  it("goes outside in, in RADIUS order, skipping a ring with no nodes", () => {
    const rings = outline(gi).map((r) => r.ring);
    const order = Object.keys(RADIUS);
    expect(rings).toEqual(order.filter((r) => FIXTURE.nodes.some((n) => n.ring === r)));
    expect(rings[0]).toBe("feeds");
    expect(rings[rings.length - 1]).toBe("book");
  });

  it("lists every node exactly once", () => {
    const all = outline(gi).flatMap((r) => r.nodes);
    expect([...all].sort((a, b) => a - b)).toEqual(FIXTURE.nodes.map((_, i) => i));
  });

  it("keeps a node on a ring the page does not know, last", () => {
    const out = structuredClone(FIXTURE) as MachineOut;
    (out.nodes[0] as { ring: string }).ring = "new-ring";
    const rings = outline(indexGraph(out));
    expect(rings[rings.length - 1]).toEqual({ ring: "elsewhere", word: "Elsewhere", nodes: [0] });
  });

  it("is empty for an empty graph", () => {
    expect(outline(indexGraph({ ...FIXTURE, nodes: [], edges: [] }))).toEqual([]);
  });
});

describe("ListView", () => {
  it("is plain HTML: a heading, a heading per ring, a disclosure per node, and no canvas", () => {
    const html = list(FIXTURE);
    expect(html).toContain("<h1>The machine as a list</h1>");
    expect(html).not.toContain("<canvas");
    expect(html.match(/<details id=/g)).toHaveLength(FIXTURE.nodes.length);
    expect(html.match(/<h2 /g)).toHaveLength(outline(indexGraph(FIXTURE)).length);
  });

  it("lists the rings' headings in the order of the outline, each linked from the top", () => {
    const html = list(FIXTURE);
    let last = -1;
    for (const { ring } of outline(indexGraph(FIXTURE))) {
      const at = html.indexOf(`id="mx-ring-${ring}"`);
      expect(at, ring).toBeGreaterThan(last);
      last = at;
      expect(html).toContain(`href="#mx-ring-${ring}"`);
    }
  });

  it("has the machine's bar: its search, under the id the / key looks for, and its status, but no web to light", () => {
    const html = list(FIXTURE);
    expect(html).toContain('id="mx-search"');
    expect(html).toContain("mx-status-time");
    expect(html).not.toContain('aria-label="Web mode"');
  });

  it("opens nothing on the bare list, and writes no reading for a closed node", () => {
    const html = list(FIXTURE);
    expect(openNodes(html)).toBe(0);
    expect(html).not.toContain("mx-node-body");
  });

  it("opens the node `?sel=` names, with its reading and what it reads and feeds as links", () => {
    const html = list(FIXTURE, "?sel=d:NVDA&view=list");
    expect(openNodes(html)).toBe(1);
    expect(html).toContain("mx-node-body");
    expect(html).toContain("reads");
    expect(html).toContain("feeds");
    expect(html).toMatch(/<a class="mx-chip" href="\/\?sel=[^"]+&amp;view=list"/);
    const body = html.slice(html.indexOf("mx-node-body"));
    expect(body).not.toContain("<button");
  });

  it("opens the node a tour address's step names, as the machine would select it", () => {
    expect(list(FIXTURE, "?step=3&view=list")).toContain("mx-node-body");
    expect(openNodes(list(FIXTURE, "?step=1&view=list"))).toBe(0);
  });

  it("takes a selection the graph does not have as no selection", () => {
    expect(openNodes(list(FIXTURE, "?sel=d:GONE&view=list"))).toBe(0);
  });

  it("writes a link for a browser that came to the list by not drawing as the plain address", () => {
    expect(list(FIXTURE, "?sel=d:NVDA")).toMatch(/<a class="mx-chip" href="\/\?sel=[^"&]+"/);
  });

  it("prints every node's reading without an empty value, on every fixture", () => {
    for (const out of [FIXTURE, FIXTURE_NO_SUMMARY, FIXTURE_BROKEN]) {
      for (const n of out.nodes) expect(list(out, `?sel=${encodeURIComponent(n.id)}&view=list`), n.id).not.toMatch(CLEAN);
    }
  });

  it("says why it stands in for the machine, only when it does", () => {
    expect(list(FIXTURE)).not.toContain('role="status"');
    expect(list(FIXTURE, "", { drawable: false, note: "No WebGL here." })).toContain('<p role="status">No WebGL here.</p>');
  });

  it("offers the way back to the machine only to a browser that can draw it", () => {
    expect(list(FIXTURE, "?sel=d:NVDA&view=list")).toContain('aria-label="The machine, drawn"');
    expect(list(FIXTURE, "?sel=d:NVDA&view=list")).toMatch(/href="\/\?sel=d:NVDA"/);
    expect(list(FIXTURE, "", { drawable: false })).not.toContain("The machine, drawn");
  });

  it("escapes text from the wire", () => {
    const out = structuredClone(FIXTURE) as MachineOut;
    out.nodes[0].n = '<img src=x onerror="alert(1)">';
    out.nodes[0].l = "<b>x</b>";
    const html = list(out, `?sel=${encodeURIComponent(out.nodes[0].id)}&view=list`);
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<b>x</b>");
  });

  it("uses no innerHTML, and imports nothing that reaches three.js", () => {
    const src = readFileSync(new URL("./ListView.tsx", import.meta.url), "utf8");
    expect(src).not.toMatch(/dangerouslySetInnerHTML|innerHTML/);
    expect(src).not.toMatch(/from "\.\/(scene|useScene|Instrument)/);
  });
});
