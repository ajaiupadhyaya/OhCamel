import { describe, expect, it } from "vitest";
import { fmtStamp } from "./stamp";

describe("fmtStamp", () => {
  it("a timestamp reads day, month and New York time", () => expect(fmtStamp("2026-10-05T20:02:00Z")).toBe("05 OCT 16:02"));
  it("a date-only value reads day, month and year", () => expect(fmtStamp("2026-10-05")).toBe("05 OCT 2026"));
  it("winter time is EST", () => expect(fmtStamp("2026-12-01T15:30:00Z")).toBe("01 DEC 10:30"));
  it("an unparseable value is shown as given, never invented", () => expect(fmtStamp("yesterday")).toBe("yesterday"));
  it("empty reads as a dash", () => expect(fmtStamp(null)).toBe("—"));
});
