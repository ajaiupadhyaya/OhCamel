import { describe, expect, it } from "vitest";
import { esVerdict } from "./shared";

describe("esVerdict (Acerbi–Szekely Z2 words and their colour)", () => {
  it("a rejection is signal, like a red zone or a rejected p-value", () => expect(esVerdict("reject (ES underestimated)")).toEqual({ word: "REJECT", tone: "loss" }));
  it("accept is ink", () => expect(esVerdict("accept")).toEqual({ word: "ACCEPT", tone: "" }));
  it("warning is secondary", () => expect(esVerdict("warning")).toEqual({ word: "WARNING", tone: "subtle" }));
});
