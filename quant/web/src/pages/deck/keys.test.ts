import { describe, expect, it } from "vitest";
import { drawerKey } from "./keys";

const k = (key: string, extra: Partial<Parameters<typeof drawerKey>[0]> = {}) => drawerKey({ key, ...extra });

describe("drawerKey", () => {
  it("toggles on a bare d or D", () => {
    expect(k("d")).toBe(true);
    expect(k("D", { shiftKey: true })).toBe(true);
  });
  it("ignores modified keys, other keys and keys the shell already consumed (the g d chord)", () => {
    expect(k("d", { metaKey: true })).toBe(false);
    expect(k("d", { ctrlKey: true })).toBe(false);
    expect(k("d", { altKey: true })).toBe(false);
    expect(k("d", { defaultPrevented: true })).toBe(false);
    expect(k("e")).toBe(false);
  });
  it("ignores typing in a field", () => {
    expect(k("d", { target: { tagName: "INPUT" } })).toBe(false);
    expect(k("d", { target: { tagName: "SELECT" } })).toBe(false);
    expect(k("d", { target: { tagName: "DIV", isContentEditable: true } })).toBe(false);
    expect(k("d", { target: { tagName: "BUTTON" } })).toBe(true);
  });
});
