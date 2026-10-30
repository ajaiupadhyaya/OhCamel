import { renderToStaticMarkup } from "react-dom/server";
import { matchPath, MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ROUTES } from "../lib/routes";
import { MachineWait } from "./MachineWait";

const html = (alert = false): string =>
  renderToStaticMarkup(
    <MemoryRouter>
      <MachineWait alert={alert}>Reading the machine…</MachineWait>
    </MemoryRouter>,
  );

describe("MachineWait", () => {
  it("holds the brand and links to the main pages under the line, so a visitor is never stranded", () => {
    const page = html();
    expect(page).toContain("Reading the machine…");
    expect(page).toContain(">OhCamel</a>");
    const links = [...page.matchAll(/href="([^"]+)"[^>]*>([^<]+)</g)].map((m) => [m[1], m[2]]);
    expect(links).toEqual([["/", "OhCamel"], ["/tape", "Tape"], ["/markets", "Markets"], ["/risk", "Risk"], ["/research", "Research"], ["/system", "System"]]);
    for (const [path] of links) expect(ROUTES.some((r) => matchPath({ path: r.path, end: true }, path)), path).toBe(true);
  });

  it("is an alert only when the read failed", () => {
    expect(html()).not.toContain('role="alert"');
    expect(html(true)).toContain('<p role="alert">Reading the machine…</p>');
  });
});
