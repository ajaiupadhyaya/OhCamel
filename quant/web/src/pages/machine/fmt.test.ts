import { describe, expect, it } from "vitest";
import { dur, hm, instant, num, nyDay, nyTime, pct, plural } from "./fmt";

const MINUS = "−";
const DASH = "—";

describe("num", () => {
  it("groups thousands the en-US way, to the digits asked", () => {
    expect(num(1234567)).toBe("1,234,567");
    expect(num(1234.5, 1)).toBe("1,234.5");
    expect(num(1234, 2)).toBe("1,234.00");
    expect(num(0)).toBe("0");
    expect(num(172.35, 1)).toBe("172.4");
  });

  it("writes a negative with the typographic minus, and drops the sign from a value that rounds to zero", () => {
    expect(num(-1234.5, 1)).toBe(`${MINUS}1,234.5`);
    expect(num(-0.2)).toBe("0");
    expect(num(-0)).toBe("0");
  });
});

describe("pct", () => {
  it("reads a fraction as a percent, one place by default", () => {
    expect(pct(0.034)).toBe("3.4%");
    expect(pct(0.4127, 0)).toBe("41%");
    expect(pct(0.4393, 2)).toBe("43.93%");
    expect(pct(0)).toBe("0.0%");
    expect(pct(12.345)).toBe("1,234.5%");
  });

  it("signs only when asked: a plus for a gain, the typographic minus for a loss", () => {
    expect(pct(0.034, 1, true)).toBe("+3.4%");
    expect(pct(-0.034, 1, true)).toBe(`${MINUS}3.4%`);
    expect(pct(0, 1, true)).toBe("+0.0%");
  });

  it("gives a loss its minus even unsigned, as a level such as volatility never has one to give", () => {
    expect(pct(-0.034)).toBe(`${MINUS}3.4%`);
    expect(pct(0.1325)).toBe("13.3%");
  });

  it("does not show a minus on a value that rounds to zero", () => {
    expect(pct(-0.0004)).toBe("0.0%");
    expect(pct(-0.0004, 1, true)).toBe("+0.0%");
    expect(pct(-0)).toBe("0.0%");
  });
});

describe("hm", () => {
  it("turns an hour into a 12-hour clock time", () => {
    expect(hm(18.5)).toBe("6:30 PM");
    expect(hm(9.5)).toBe("9:30 AM");
    expect(hm(9.3333)).toBe("9:20 AM");
    expect(hm(0)).toBe("12:00 AM");
    expect(hm(12)).toBe("12:00 PM");
    expect(hm(15.75)).toBe("3:45 PM");
  });

  it("carries a rounded-up minute into the hour, and wraps round the day", () => {
    expect(hm(9.9999)).toBe("10:00 AM");
    expect(hm(23.99)).toBe("11:59 PM");
    expect(hm(23.9999)).toBe("12:00 AM");
    expect(hm(24)).toBe("12:00 AM");
    expect(hm(25.25)).toBe("1:15 AM");
    expect(hm(-0.5)).toBe("11:30 PM");
  });
});

describe("dur", () => {
  it("says seconds under a minute, minutes up to two hours, and hours with the minutes after", () => {
    expect(dur(40)).toBe("40 s");
    expect(dur(3846)).toBe("64 min");
    expect(dur(7300)).toBe("2 h 2 min");
    expect(dur(0)).toBe("0 s");
    expect(dur(5400)).toBe("90 min");
    expect(dur(7100)).toBe("118 min");
    expect(dur(9000)).toBe("2 h 30 min");
  });

  it("leaves off the minutes of a whole number of hours", () => {
    expect(dur(7200)).toBe("2 h");
    expect(dur(10800)).toBe("3 h");
  });

  it("rounds before it picks the unit, so it never says 60 s or 120 min", () => {
    expect(dur(59.4)).toBe("59 s");
    expect(dur(59.6)).toBe("1 min");
    expect(dur(60)).toBe("1 min");
    expect(dur(7169)).toBe("119 min");
    expect(dur(7170)).toBe("2 h");
    expect(dur(7199)).toBe("2 h");
  });

  it("reads a negative span (an end before its start) as no time", () => {
    expect(dur(-5)).toBe("0 s");
  });
});

describe("plural", () => {
  it("counts a noun, singular for exactly one", () => {
    expect(plural(1, "job")).toBe("1 job");
    expect(plural(2, "job")).toBe("2 jobs");
    expect(plural(0, "job")).toBe("0 jobs");
  });

  it("groups the count, and takes an irregular plural", () => {
    expect(plural(1234, "series", "series")).toBe("1,234 series");
    expect(plural(1, "strategy", "strategies")).toBe("1 strategy");
    expect(plural(2, "strategy", "strategies")).toBe("2 strategies");
  });
});

describe("a missing or non-finite value", () => {
  const formatters: [string, (x: number | null | undefined) => string][] = [
    ["num", (x) => num(x)],
    ["pct", (x) => pct(x)],
    ["pct signed", (x) => pct(x, 1, true)],
    ["hm", (x) => hm(x)],
    ["dur", (x) => dur(x)],
    ["plural", (x) => plural(x, "job")],
  ];

  it.each(formatters)("is an em dash from %s, for null, undefined, NaN and both infinities, never the word NaN", (_name, f) => {
    for (const x of [null, undefined, NaN, Infinity, -Infinity]) {
      const s = f(x);
      expect(s).toBe(DASH);
      expect(s).not.toContain("NaN");
    }
  });

  it("is a dash only for those: zero is a value", () => {
    expect(num(0)).not.toBe(DASH);
    expect(pct(0)).not.toBe(DASH);
    expect(hm(0)).not.toBe(DASH);
    expect(dur(0)).not.toBe(DASH);
    expect(plural(0, "job")).not.toBe(DASH);
  });
});

describe("nyDay and nyTime", () => {
  it("write an instant as its New York day and time of day (an evening UTC instant is the day before in New York)", () => {
    expect(nyDay("2026-10-28T01:48:11.904Z")).toBe("27 Oct");
    expect(nyTime("2026-10-28T01:48:11.904Z")).toBe("9:48 PM");
    expect(nyDay("2026-10-28T20:30:12Z")).toBe("28 Oct");
    expect(nyTime("2026-10-28T20:30:12Z")).toBe("4:30 PM");
    expect(nyTime("2026-10-28T22:51:12.388114+00:00")).toBe("6:51 PM");
  });

  it("read a timestamp with no offset as UTC, as the warehouse writes last_ok_at (warehouse/freshness.py)", () => {
    expect(nyTime("2026-10-28T22:33:38.771204")).toBe("6:33 PM");
    expect(nyDay("2026-10-28T22:33:38.771204")).toBe("28 Oct");
  });

  it("follow New York's clock across daylight saving", () => {
    expect(nyTime("2026-01-15T14:30:00Z")).toBe("9:30 AM");
    expect(nyTime("2026-07-15T13:30:00Z")).toBe("9:30 AM");
  });

  it("take a date as the calendar day it names, in no time zone, and give it no time of day", () => {
    expect(nyDay("2026-10-27")).toBe("27 Oct");
    expect(nyDay("2026-01-01")).toBe("1 Jan");
    expect(nyTime("2026-10-27")).toBe(DASH);
  });

  it("are an em dash for anything that is not a date", () => {
    for (const v of [null, undefined, "", "yesterday", "2026-13-45", 42]) {
      expect(nyDay(v as string)).toBe(DASH);
      expect(nyTime(v as string)).toBe(DASH);
    }
  });
});

describe("instant", () => {
  it("reads a timestamp with an offset as given, and one without as UTC", () => {
    expect(instant("2026-10-28T20:30:12Z")).toBe(Date.UTC(2026, 9, 28, 20, 30, 12));
    expect(instant("2026-10-28T16:30:12-04:00")).toBe(Date.UTC(2026, 9, 28, 20, 30, 12));
    expect(instant("2026-10-28T22:33:38.771204")).toBe(Date.UTC(2026, 9, 28, 22, 33, 38, 771));
  });

  it("is null for a date (a day, not an instant) and for what does not parse", () => {
    for (const v of [null, undefined, "", "2026-10-28", "soon"]) expect(instant(v)).toBeNull();
  });
});
