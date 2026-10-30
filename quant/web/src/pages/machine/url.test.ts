import { describe, expect, it } from "vitest";
import { FIXTURE } from "./fixture";
import { parseView, TABS, viewSearch } from "./url";
import type { Tab, View } from "./url";

const KNOWN = new Set(FIXTURE.nodes.map((n) => n.id));
const has = (id: string): boolean => KNOWN.has(id);
const DEFAULT: View = { sel: null, tab: "tour", step: 1 };

describe("parseView: a view from the address", () => {
  it("is the default view for an address with nothing in it", () => {
    for (const s of ["", "?", "?&&", "?other=1", "?unknown=d:NVDA"]) expect(parseView(s, has)).toEqual(DEFAULT);
  });

  it("reads a node that exists, a tab and a step", () => {
    expect(parseView("?sel=d:NVDA&tab=read&step=3", has)).toEqual({ sel: "d:NVDA", tab: "read", step: 3 });
    expect(parseView("?sel=j:farm.sweep", has)).toEqual({ sel: "j:farm.sweep", tab: "tour", step: 1 });
    expect(parseView("?tab=day", has)).toEqual({ sel: null, tab: "day", step: 1 });
    expect(parseView("?step=8", has)).toEqual({ sel: null, tab: "tour", step: 8 });
  });

  it("reads the address with or without its question mark, and a percent-encoded id", () => {
    expect(parseView("sel=d:NVDA&tab=read", has)).toEqual({ sel: "d:NVDA", tab: "read", step: 1 });
    expect(parseView("?sel=d%3ANVDA", has).sel).toBe("d:NVDA");
    expect(parseView("?sel=c%3Atsmom%40us_equity_indices", has).sel).toBe("c:tsmom@us_equity_indices");
    expect(parseView("?sel=c:tsmom@us_equity_indices", has).sel).toBe("c:tsmom@us_equity_indices");
  });

  it("takes the first when a field is given twice", () => {
    expect(parseView("?sel=d:NVDA&sel=book&tab=read&tab=day&step=2&step=5", has)).toEqual({ sel: "d:NVDA", tab: "read", step: 2 });
  });

  describe("sel: only a node that exists (Review Focus 4)", () => {
    it("falls back to nothing for a node the graph does not have, and the rest of the view stays", () => {
      expect(parseView("?sel=d:NOPE&tab=read&step=3", has)).toEqual({ sel: null, tab: "read", step: 3 });
    });

    it("falls back to nothing for hostile text", () => {
      const hostile = [
        "<img src=x onerror=alert(1)>",
        "\"><script>alert(1)</script>",
        "javascript:alert(1)",
        "'; DROP TABLE nodes; --",
        "../../etc/passwd",
        "__proto__",
        "constructor",
        "toString",
        "d:NVDA<script>",
        "d:nvda",
        " d:NVDA",
        "d:NVDA ",
        "\u0000",
        "%00",
        "a".repeat(100_000),
      ];
      for (const h of hostile) {
        expect(parseView(`?sel=${encodeURIComponent(h)}`, has).sel).toBeNull();
      }
      expect(parseView("?sel=<img src=x onerror=alert(1)>", has)).toEqual(DEFAULT);
      expect(parseView("?sel=<img src=x onerror=alert(1)>&tab=day&step=4", has)).toEqual({ sel: null, tab: "day", step: 4 });
      expect(parseView("?sel=<script>alert(1)</script>&tab=read", has)).toEqual({ sel: null, tab: "read", step: 1 });
    });

    it("falls back to nothing for an empty sel, even if every id would pass", () => {
      expect(parseView("?sel=", () => true).sel).toBeNull();
      expect(parseView("?sel=&tab=read", () => true)).toEqual({ sel: null, tab: "read", step: 1 });
    });

    it("asks the graph about the text it was given and nothing else", () => {
      const asked: string[] = [];
      parseView("?sel=d:NVDA&tab=read", (id) => (asked.push(id), true));
      expect(asked).toEqual(["d:NVDA"]);
      asked.length = 0;
      parseView("?tab=read&step=2", (id) => (asked.push(id), true));
      expect(asked).toEqual([]);
    });
  });

  describe("tab: one of the three", () => {
    it.each(["tour", "read", "day"] as const)("reads %s", (tab) => {
      expect(parseView(`?tab=${tab}`, has).tab).toBe(tab);
    });

    it.each(["", "READ", "Day", "reading", "reads", "days", "tour,read", "tour ", " tour", "map", "__proto__", "constructor", "toString", "0", "<b>"])(
      "falls back to the tour for %j, and the rest of the view stays",
      (tab) => {
        expect(parseView(`?sel=book&tab=${encodeURIComponent(tab)}&step=6`, has)).toEqual({ sel: "book", tab: "tour", step: 6 });
      },
    );
  });

  describe("step: an integer 1 to 8", () => {
    it.each([1, 2, 3, 4, 5, 6, 7, 8])("reads %i", (step) => {
      expect(parseView(`?step=${step}`, has).step).toBe(step);
    });

    it.each(["0", "9", "10", "99", "-1", "-0", "1.5", "2.0", "3e0", "0x3", "+3", " 3", "3 ", "3abc", "abc", "", "NaN", "Infinity", "-Infinity", "1000000000000000000000", "٣"])(
      "falls back to 1 for %j, and the rest of the view stays",
      (step) => {
        expect(parseView(`?sel=book&tab=day&step=${encodeURIComponent(step)}`, has)).toEqual({ sel: "book", tab: "day", step: 1 });
      },
    );

    it("falls back to 1 for ?step=99", () => {
      expect(parseView("?step=99", has)).toEqual(DEFAULT);
    });

    it("reads a step written with leading zeros, which is the integer it names", () => {
      expect(parseView("?step=03", has).step).toBe(3);
      expect(parseView("?step=000", has).step).toBe(1);
      expect(parseView("?step=09", has).step).toBe(1);
    });
  });

  it("falls back field by field: each bad field alone leaves the other two", () => {
    expect(parseView("?sel=d:NOPE&tab=day&step=5", has)).toEqual({ sel: null, tab: "day", step: 5 });
    expect(parseView("?sel=book&tab=nope&step=5", has)).toEqual({ sel: "book", tab: "tour", step: 5 });
    expect(parseView("?sel=book&tab=day&step=nope", has)).toEqual({ sel: "book", tab: "day", step: 1 });
    expect(parseView("?sel=nope&tab=nope&step=nope", has)).toEqual(DEFAULT);
  });

  describe("malformed percent-encoding never throws", () => {
    const bad = ["%E0%A4%A", "%", "%%", "%zz", "%E0%A4", "%C0%AF", "%ED%A0%80", "%FF", "%u0041", "%2", "100%"];
    it.each(bad)("%s in each field", (b) => {
      expect(() => parseView(`?sel=${b}&tab=${b}&step=${b}`, has)).not.toThrow();
      expect(parseView(`?sel=${b}&tab=${b}&step=${b}`, has)).toEqual(DEFAULT);
    });

    it("leaves the other fields alone", () => {
      expect(parseView("?sel=%E0%A4%A&tab=read&step=3", has)).toEqual({ sel: null, tab: "read", step: 3 });
      expect(parseView("?sel=book&tab=%E0%A4%A&step=3", has)).toEqual({ sel: "book", tab: "tour", step: 3 });
      expect(parseView("?sel=book&tab=read&step=%E0%A4%A", has)).toEqual({ sel: "book", tab: "read", step: 1 });
    });

    it("does not throw on a key that is malformed either", () => {
      expect(() => parseView("?%E0%A4%A=1&%=2&=3&&===", has)).not.toThrow();
    });
  });

  it("is safe on input that is not text", () => {
    for (const s of [undefined, null, 7, {}, []]) expect(parseView(s as unknown as string, has)).toEqual(DEFAULT);
  });

  it("returns a new view each call, and a view that can be changed freely", () => {
    const a = parseView("", has), b = parseView("", has);
    expect(a).not.toBe(b);
    a.step = 5;
    expect(parseView("", has)).toEqual(DEFAULT);
  });
});

describe("viewSearch: the address of a view", () => {
  it("is empty for the default view: it omits every default", () => {
    expect(viewSearch(DEFAULT)).toBe("");
  });

  it("writes only what is not the default", () => {
    expect(viewSearch({ sel: "d:NVDA", tab: "tour", step: 1 })).toBe("?sel=d:NVDA");
    expect(viewSearch({ sel: null, tab: "read", step: 1 })).toBe("?tab=read");
    expect(viewSearch({ sel: null, tab: "tour", step: 4 })).toBe("?step=4");
    expect(viewSearch({ sel: null, tab: "read", step: 3 })).toBe("?tab=read&step=3");
    expect(viewSearch({ sel: "book", tab: "day", step: 8 })).toBe("?sel=book&tab=day&step=8");
  });

  it("keeps the step on the other tabs, so coming back to the tour finds its place", () => {
    expect(viewSearch({ sel: "book", tab: "read", step: 5 })).toBe("?sel=book&tab=read&step=5");
  });

  it("writes an id as readable as it can be, and encodes every character that would break the address", () => {
    expect(viewSearch({ sel: "c:tsmom@us_equity_indices", tab: "tour", step: 1 })).toBe("?sel=c:tsmom@us_equity_indices");
    expect(viewSearch({ sel: "j:farm.sweep", tab: "tour", step: 1 })).toBe("?sel=j:farm.sweep");
    expect(viewSearch({ sel: "a b&c=d#e%f+g?h/i", tab: "tour", step: 1 })).toBe("?sel=a%20b%26c%3Dd%23e%25f%2Bg%3Fh%2Fi");
    expect(viewSearch({ sel: "<img src=x onerror=alert(1)>", tab: "tour", step: 1 }).slice("?sel=".length)).not.toMatch(/[<> "'=]/);
  });

  it("omits an empty sel, and a tab or step the address would not accept back", () => {
    expect(viewSearch({ sel: "", tab: "tour", step: 1 })).toBe("");
    expect(viewSearch({ sel: null, tab: "nope" as Tab, step: 1 })).toBe("");
    for (const step of [0, 9, 99, -1, 2.5, Number.NaN, Infinity]) expect(viewSearch({ sel: null, tab: "tour", step })).toBe("");
  });
});

describe("the round trip", () => {
  it("restores every view of every node, tab and step", () => {
    for (const n of [null, ...FIXTURE.nodes.map((x) => x.id)]) {
      for (const tab of TABS) {
        for (let step = 1; step <= 8; step++) {
          const v: View = { sel: n, tab, step };
          expect(parseView(viewSearch(v), has)).toEqual(v);
        }
      }
    }
  });

  it("restores an id with characters that need encoding", () => {
    const odd = ["lim:a b", "lim:a&b=c", "lim:a+b", "lim:100%", "lim:a#b", "lim:a?b/c", "lim:日本語", "lim:é", "lim:a\"b'c", "lim:<b>"];
    const hasOdd = (id: string): boolean => odd.includes(id);
    for (const sel of odd) {
      const v: View = { sel, tab: "read", step: 2 };
      expect(parseView(viewSearch(v), hasOdd)).toEqual(v);
    }
  });

  it("is stable: writing what was read gives the same address again", () => {
    const inputs = [
      "", "?", "?sel=d:NVDA", "?sel=d%3ANVDA&tab=read&step=3", "?sel=d:NOPE&tab=read&step=3",
      "?sel=<img src=x onerror=alert(1)>", "?step=99", "?step=1&tab=tour", "?tab=TOUR&sel=", "?step=03&tab=day", "?sel=%E0%A4%A&tab=day&step=%",
      "?tab=day&sel=book&step=8&extra=1", "sel=book", "?step=2&step=5&sel=kill",
    ];
    for (const s of inputs) {
      const v = parseView(s, has);
      const out = viewSearch(v);
      expect(parseView(out, has)).toEqual(v);
      expect(viewSearch(parseView(out, has))).toBe(out);
    }
  });

  it("writes a normal form: the same view is the same address whatever order it was read in", () => {
    expect(viewSearch(parseView("?step=3&tab=read&sel=book", has))).toBe("?sel=book&tab=read&step=3");
    expect(viewSearch(parseView("?sel=book&tab=read&step=3", has))).toBe("?sel=book&tab=read&step=3");
  });
});
