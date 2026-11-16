import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, DataUnavailableError, NetworkError } from "../../lib/api";

// The page reads GET /api/ledger and each product's latest artifact through useApiQuery; the tests hand it answers.
const answers = vi.hoisted(() => ({ ledger: {} as Record<string, unknown> }));
vi.mock("../../lib/query", () => ({
  useApiQuery: (path: string) =>
    path === "/ledger" ? answers.ledger : { data: undefined, error: null, isLoading: true, isError: false },
}));

import Ledger, { LedgerSections } from "../Ledger";
import { bySection, ledgerAbsent, ledgerStatus, rowMark, type LedgerOut, type LedgerRow } from "./ledger";

const row = (over: Partial<LedgerRow>): LedgerRow => ({
  section: "BUILT", item: "X", detail: "d.", state: "static", source: "", checked: "2026-11-05", stale: false,
  kind: null, methodology: null, ...over,
});

const LEDGER: LedgerOut = {
  as_of: "2026-11-16",
  stale_after_days: 45,
  rows: [
    row({ item: "P1 RISK ATLAS", kind: "risk.mc_atlas", methodology: "p1-mc-atlas" }),
    row({ section: "CUT", item: "KIOSK MODE", checked: "2026-09-17", stale: true, detail: "A wall-display mode." }),
    row({ section: "ADVISORY", item: "NO LIVE STRATEGY", state: "ok", checked: null, source: "desk.report 2026-11-13 · pnl_by_strategy",
          detail: "22 strategies are registered on the paper book: 22 incubation. None is live." }),
    row({ section: "ADVISORY", item: "DESK HALTED", state: "attention", checked: null, source: "desk.report 2026-11-13 · book_summary",
          detail: "The desk is halted by hand: nothing trades until the owner lifts the halt." }),
    row({ section: "LIMITS", item: "BACKUP STATE UNKNOWN", state: "unknown", checked: null, source: "the backup's record · ops/backup.json",
          detail: "Unknown: no record of the nightly backup is in this server's volume." }),
    row({ section: "OWNER", item: "BOOK LIMITS", state: "ok", checked: null, source: "desk.report 2026-11-13 · book_summary",
          detail: "The owner sets the book's 3 limits in book.sexp." }),
  ],
  provenance: [],
  notes: [],
};

function render(node: ReactNode) {
  return renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
}

describe("the Ledger's helpers", () => {
  it("numbers each section's rows in the server's order, every section present", () => {
    const s = bySection(LEDGER.rows);
    expect(s.map((x) => x.section)).toEqual(["BUILT", "CUT", "ADVISORY", "LIMITS", "OWNER"]);
    expect(s[2].rows.map((r) => [r.n, r.item])).toEqual([[1, "NO LIVE STRATEGY"], [2, "DESK HALTED"]]);
  });
  it("marks a prose row with its checked date, and STALE past the server's age", () => {
    expect(rowMark(LEDGER.rows[0])).toEqual({ text: "CHECKED 05 NOV 2026", cls: "lg-mark" });
    expect(rowMark(LEDGER.rows[1])).toEqual({ text: "CHECKED 17 SEP 2026 · STALE", cls: "lg-mark lg-mark-stale" });
  });
  it("marks a computed row with its state and its source", () => {
    expect(rowMark(LEDGER.rows[2]).text).toBe("READ · desk.report 2026-11-13 · pnl_by_strategy");
    expect(rowMark(LEDGER.rows[3])).toEqual({ text: "ATTENTION · desk.report 2026-11-13 · book_summary", cls: "lg-mark lg-mark-attention" });
    expect(rowMark(LEDGER.rows[4]).text).toBe("UNKNOWN · the backup's record · ops/backup.json");
  });
  it("says why the ledger could not be read", () => {
    expect(ledgerAbsent(new DataUnavailableError("down", "/ledger"))).toBe("DATA UNAVAILABLE");
    expect(ledgerAbsent(new NetworkError("offline", "/ledger"))).toBe("API UNREACHABLE");
    expect(ledgerAbsent(new ApiError(404, "no", "/ledger"))).toBe("NOT SERVED");
    expect(ledgerAbsent(new ApiError(500, "boom", "/ledger"))).toBe("UNREADABLE");
  });
  it("gives the System index the date, the count and what needs a look", () => {
    expect(ledgerStatus(LEDGER, null, false)).toBe("AS OF 16 NOV 2026 · 6 ENTRIES · 1 ATTENTION · 1 UNKNOWN · 1 STALE");
    expect(ledgerStatus(undefined, null, true)).toBe("LEDGER …");
    expect(ledgerStatus(undefined, new NetworkError("offline", "/ledger"), false)).toBe("LEDGER API UNREACHABLE");
  });
});

describe("the Ledger page", () => {
  beforeEach(() => {
    answers.ledger = { data: LEDGER, error: null, isLoading: false, isError: false };
  });

  it("renders what the server built, dated by the server", () => {
    const html = render(<Ledger />);
    expect(html).toContain("AS OF 16 NOV 2026");
    expect(html).toContain("6 ENTRIES");
    expect(html).toContain("22 strategies are registered on the paper book");
    expect(html).toContain("ADVISORY · 2");
    expect(html).not.toContain("No strategy on this site trades");
  });
  it("hatches a stale prose row and only that one", () => {
    const html = render(<LedgerSections data={LEDGER} />);
    expect(html.match(/lg-hatch/g)).toHaveLength(2); // its item cell and its detail cell
    const kiosk = html.indexOf("KIOSK MODE");
    expect(html.lastIndexOf("lg-hatch", kiosk)).toBeGreaterThan(html.lastIndexOf("<tr", kiosk));
  });
  it("sets a row needing attention in the signal colour and links a product to its Methodology entry", () => {
    const html = render(<LedgerSections data={LEDGER} />);
    expect(html).toContain('class="lg-item lg-item-attention">DESK HALTED');
    expect(html).toContain('href="/methodology#p1-mc-atlas"');
  });
  it("says a section is empty rather than drawing an empty table", () => {
    const html = render(<LedgerSections data={{ ...LEDGER, rows: LEDGER.rows.filter((r) => r.section !== "CUT") }} />);
    expect(html).toContain("CUT · 0");
    expect(html).toContain("NO ROWS");
  });
  it("says the ledger could not be read when the API is down, and shows no rows of its own", () => {
    answers.ledger = { data: undefined, error: new NetworkError("offline", "/ledger"), isLoading: false, isError: true };
    const html = render(<Ledger />);
    expect(html).toContain("API UNREACHABLE");
    expect(html).toContain("GET /api/ledger");
    expect(html).toContain("AS OF —");
    expect(html).not.toContain("BUILT ·");
  });
  it("says it is reading while the request is in flight", () => {
    answers.ledger = { data: undefined, error: null, isLoading: true, isError: false };
    expect(render(<Ledger />)).toContain("READING");
  });
});
