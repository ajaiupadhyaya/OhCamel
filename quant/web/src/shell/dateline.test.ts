import { describe, expect, it } from "vitest";
import { formatDateline } from "./dateline";

describe("formatDateline", () => {
  it("renders New York time, uppercase, with the session", () => {
    const d = new Date("2026-10-05T20:00:00Z"); // 16:00 New York (EDT)
    expect(formatDateline(d, "closed")).toBe("MON 05 OCT 2026 · 16:00 ET · SESSION CLOSED");
  });
  it("handles the DST change", () => {
    const d = new Date("2026-11-02T21:00:00Z"); // 16:00 New York (EST)
    expect(formatDateline(d, "open")).toBe("MON 02 NOV 2026 · 16:00 ET · SESSION OPEN");
  });
  it("says UNKNOWN rather than guessing", () => {
    expect(formatDateline(new Date("2026-10-05T14:00:00Z"), "unknown")).toMatch(/SESSION UNKNOWN$/);
  });
});
