import { describe, expect, it } from "vitest";
import { FIXTURE } from "./fixture";
import { parseDailyNote, parseOverlayFallback, parseOverlayNote } from "./notes";

const notesOf = (id: string): string[] => FIXTURE.nodes.find((n) => n.id === id)!.m.notes as string[];

describe("parseDailyNote", () => {
  it("reads the six counts from daily signals' first note (signals/daily.py:482)", () => {
    expect(parseDailyNote(notesOf("p:signals.daily"))).toEqual({ written: 0, late: 0, again: 1, held: 17, present: 0, refused: 3 });
  });

  it("reads multi-digit counts and ignores the notes after the first", () => {
    const note = "12 written, 3 written late, 40 written again (the desk did not size the last), 117 held, 2 already present, 0 refused";
    expect(parseDailyNote([note, "1 written, 1 written late"])).toEqual({ written: 12, late: 3, again: 40, held: 117, present: 2, refused: 0 });
  });

  it("is null for no notes, a first note of another shape, or a count note that is not first", () => {
    expect(parseDailyNote([])).toBeNull();
    expect(parseDailyNote(["no targets yet"])).toBeNull();
    expect(parseDailyNote(["written, written late, written again, held, already present, refused"])).toBeNull();
    expect(parseDailyNote(["a caveat", "0 written, 0 written late, 1 written again (x), 17 held, 0 already present, 3 refused"])).toBeNull();
  });
});

describe("parseOverlayNote", () => {
  it("reads the scale, target, forecast, estimator and sessions from the exposure note (signals/overlay.py:430)", () => {
    expect(parseOverlayNote(notesOf("p:signals.overlay"))).toEqual({
      scale: 1, target: 0.1, forecast: 0.033877, estimator: "lw_constant_corr", sessions: 504,
    });
  });

  it("finds the exposure note after other notes, and reads one with no covariance clause", () => {
    const got = parseOverlayNote(["cov.league X has no rank-1 row for site_etfs: lw_constant_corr", "exposure_scale 0.731000 = min(1, 0.1 / 0.136799)"]);
    expect(got).toEqual({ scale: 0.731, target: 0.1, forecast: 0.136799, estimator: null, sessions: null });
  });

  it("is null when no note is an exposure note", () => {
    expect(parseOverlayNote([])).toBeNull();
    expect(parseOverlayNote(["no targets yet: no roster document in the signals directory, exposure_scale 1"])).toBeNull();
    expect(parseOverlayNote(["exposure_scale = min(1, target / forecast)"])).toBeNull();
  });
});

describe("parseOverlayFallback", () => {
  it("names the estimator the overlay fell back to, when a note says it did (signals/overlay.py:240, 246)", () => {
    expect(parseOverlayFallback(["cov.league 01M5 has no rank-1 row for site_etfs: lw_constant_corr"])).toBe("lw_constant_corr");
    expect(parseOverlayFallback(["x", "no cov.league artifact: lw_constant_corr (Ledoit-Wolf)"])).toBe("lw_constant_corr");
  });

  it("is null when the covariance league gave the winner", () => {
    expect(parseOverlayFallback(notesOf("p:signals.overlay"))).toBeNull();
    expect(parseOverlayFallback([])).toBeNull();
  });
});
