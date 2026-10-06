import { describe, expect, it } from "vitest";
import { normalizeTheme } from "./theme";

describe("normalizeTheme", () => {
  it("maps the old stored names onto paper tape", () => {
    expect(normalizeTheme("dark")).toBe("carbon");
    expect(normalizeTheme("light")).toBe("paper");
  });
  it("keeps the new names", () => {
    expect(normalizeTheme("carbon")).toBe("carbon");
    expect(normalizeTheme("paper")).toBe("paper");
  });
  it("anything else is no explicit choice", () => {
    expect(normalizeTheme("x")).toBeNull();
    expect(normalizeTheme(null)).toBeNull();
    expect(normalizeTheme("system")).toBeNull();
  });
});
