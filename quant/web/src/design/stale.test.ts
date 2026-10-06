import { describe, expect, it } from "vitest";
import { isStale } from "./stale";

const now = new Date("2026-10-05T20:00:00Z");
describe("isStale (Review Focus 1)", () => {
  it("fresh inside the max age", () => expect(isStale("2026-10-05T19:30:00Z", 3600, now)).toBe(false));
  it("stale past the max age", () => expect(isStale("2026-10-04T19:30:00Z", 3600, now)).toBe(true));
  it("an unknown asOf is stale, never fresh", () => expect(isStale(null, 3600, now)).toBe(true));
  it("an unparseable asOf is stale", () => expect(isStale("yesterday", 3600, now)).toBe(true));
  it("a future asOf is stale (clock skew is not freshness)", () => expect(isStale("2026-10-06T20:00:00Z", 3600, now)).toBe(true));
  it("date-only asOf counts from the end of that New York day", () => expect(isStale("2026-10-05", 86400, now)).toBe(false));
});
