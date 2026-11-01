import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

const ruined = vi.hoisted(() => ({ date: "2020-11-05", return: -1.583 }));
vi.mock("../../lib/query", async (orig) => ({
  ...(await orig<typeof import("../../lib/query")>()),
  useApiPost: () => ({ data: { portfolio: { end: "2026-10-30", ruined }, summary: {} }, isLoading: false, isError: false, refetch: () => {} }),
}));
import { OverviewTab } from "./OverviewTab";

describe("OverviewTab on a book that lost all its equity", () => {
  const html = () => renderToStaticMarkup(<OverviewTab req={{ holdings: [{ ticker: "PLTR", weight: -6.7 }] }} />);

  it("says so, with the day and the loss", () => {
    expect(html()).toContain("5 Nov 2020");
    expect(html()).toContain("158");
  });

  it("shows no compounded figure past that day", () => {
    expect(html()).not.toContain("CAGR");
    expect(html()).not.toContain("Under water");
  });
});
