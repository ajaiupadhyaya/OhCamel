import { describe, expect, test } from "vitest";
import { ageCounter, litSegments, moneyCounter, sessionCountdown } from "./segments";

describe("seven-segment counters", () => {
  test("glyphs", () => {
    expect(litSegments("8")).toBe("abcdefg");
    expect(litSegments("1")).toBe("bc");
    expect(litSegments("-")).toBe("g");
    expect(litSegments("x")).toBe("");
  });

  test("money: whole dollars, zero-padded, signed", () => {
    expect(moneyCounter(4000.4)).toMatchObject({ digits: "004000", unit: "USD", sign: "gain", text: "$4,000" });
    expect(moneyCounter(-12345.6)).toMatchObject({ digits: "-012346", sign: "loss", text: "minus $12,346" });
    expect(moneyCounter(1_234_567)).toMatchObject({ digits: "1234567" });
    expect(moneyCounter(-0.2)).toMatchObject({ digits: "000000", sign: null });
    expect(moneyCounter(2.5e9)).toMatchObject({ digits: "2500000", unit: "USD thousands" });
    expect(moneyCounter(null)).toMatchObject({ digits: "------", text: "not available" });
  });

  test("age: MM:SS under an hour, HH:MM under a hundred hours", () => {
    expect(ageCounter(75)).toMatchObject({ digits: "01:15", unit: "min:s" });
    expect(ageCounter(0)).toMatchObject({ digits: "00:00" });
    expect(ageCounter(3600 * 5 + 60 * 7 + 30)).toMatchObject({ digits: "05:07", unit: "h:min" });
    expect(ageCounter(3600 * 120)).toMatchObject({ digits: "--:--", text: "over 99 hours" });
    expect(ageCounter(null)).toMatchObject({ digits: "--:--" });
  });

  test("session countdown: to the close while open, to the open while closed", () => {
    const now = Date.parse("2026-09-24T14:31:20Z");
    const open = { is_open: true, next_close: "2026-09-24T20:00:00Z", next_open: "2026-09-25T13:30:00Z" };
    expect(sessionCountdown(open, now)).toMatchObject({ digits: "05:29", label: "to the close" }); // 5 h 28 min 40 s, rounded up
    const closed = { ...open, is_open: false };
    expect(sessionCountdown(closed, now)).toMatchObject({ digits: "22:59", label: "to the open" });
    expect(sessionCountdown({ ...open, next_close: "2026-09-24T14:00:00Z" }, now).digits).toBe("00:00");
    expect(sessionCountdown({ is_open: false, next_close: null, next_open: "2026-10-01T13:30:00Z" }, now).digits).toBe("--:--");
    expect(sessionCountdown(null, now)).toMatchObject({ digits: "--:--", label: "session clock" });
    expect(sessionCountdown({ ...open, next_close: null }, now).digits).toBe("--:--");
  });
});
