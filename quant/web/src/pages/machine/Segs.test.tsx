import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Paras, Segs } from "./Segs";
import type { Seg } from "./prose";

const html = (segs: Seg[], has: (id: string) => boolean = () => true): string =>
  renderToStaticMarkup(<Segs segs={segs} has={has} onSelect={() => {}} />);

describe("Segs", () => {
  it("writes plain words as they are", () => {
    expect(html([{ t: "the book" }])).toBe("the book");
  });

  it("writes a figure as a bold figure in the mono class", () => {
    expect(html([{ b: "3.4%" }])).toBe('<b class="mx-fig">3.4%</b>');
  });

  it("writes a ref to a node as a button", () => {
    const out = html([{ ref: "d:NVDA", t: "NVDA" }]);
    expect(out).toContain("<button");
    expect(out).toContain(">NVDA</button>");
  });

  it("gives a link its own text as a title only when asked, for a name a table may cut short", () => {
    const segs: Seg[] = [{ ref: "c:tsmom@rates", t: "tsmom on rates" }];
    expect(html(segs)).not.toContain("title=");
    expect(renderToStaticMarkup(<Segs segs={segs} has={() => true} onSelect={() => {}} titled />)).toContain('title="tsmom on rates"');
  });

  it("writes a ref as the page's link when it has one, and plain text when the node is not in the graph", () => {
    const link = (id: string, t: string) => <a href={`?sel=${id}`}>{t}</a>;
    const out = renderToStaticMarkup(<Segs segs={[{ ref: "d:NVDA", t: "NVDA" }, { ref: "d:GONE", t: "GONE" }]} has={(id) => id === "d:NVDA"} onSelect={() => {}} link={link} />);
    expect(out).toBe('<a href="?sel=d:NVDA">NVDA</a>GONE');
  });

  it("writes a ref to a node that is not in the graph as plain text", () => {
    expect(html([{ ref: "d:GONE", t: "GONE" }], () => false)).toBe("GONE");
  });

  it("escapes text from the wire, in every kind of segment", () => {
    const evil = '<img src=x onerror="alert(1)">';
    const out = html([{ t: evil }, { b: evil }, { ref: "d:NVDA", t: evil }]);
    expect(out).not.toContain("<img");
    expect(out.match(/&lt;img/g)).toHaveLength(3);
  });
});

describe("Paras", () => {
  it("gives each paragraph its own <p>", () => {
    const out = renderToStaticMarkup(<Paras paras={[[{ t: "one" }], [{ t: "two" }]]} has={() => true} onSelect={() => {}} />);
    expect(out).toBe("<p>one</p><p>two</p>");
  });
});
