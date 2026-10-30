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
