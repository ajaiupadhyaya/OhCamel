import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

const cwd = new URL("..", import.meta.url).pathname;
const eslint = new ESLint({ cwd });

async function restricted(file: string, text: string): Promise<string[]> {
  const [res] = await eslint.lintText(text, { filePath: `${cwd}${file}` });
  return res.messages.filter((m) => m.ruleId === "no-restricted-syntax").map((m) => m.message);
}

describe("eslint: numbers through lib/format, no inline style (P2-1)", () => {
  it("flags .toFixed in a page", async () => {
    expect(await restricted("src/pages/markets/X.tsx", "export const f = (x: number) => x.toFixed(2);\n")).toHaveLength(1);
  });
  it("allows .toFixed inside lib/format.ts", async () => {
    expect(await restricted("src/lib/format.ts", "export const f = (x: number) => x.toFixed(2);\n")).toHaveLength(0);
  });
  it("flags an inline style with a CSS property", async () => {
    expect(await restricted("src/pages/markets/X.tsx", "export const A = () => <div style={{ height: 3 }} />;\n")).toHaveLength(1);
  });
  it("flags a style object passed by reference", async () => {
    expect(await restricted("src/pages/markets/X.tsx", "const s = { height: 3 };\nexport const A = () => <div style={s} />;\n")).toHaveLength(1);
  });
  it("allows CSS custom properties", async () => {
    expect(await restricted("src/pages/markets/X.tsx", 'export const A = () => <div style={{ "--pos": "40%" } as React.CSSProperties} />;\n')).toHaveLength(0);
  });
  it("a new file is covered by default", async () => {
    expect(await restricted("src/pages/NewPage.tsx", "export const A = () => <div style={{ color: 'red' }} />;\n")).toHaveLength(1);
  });
});
