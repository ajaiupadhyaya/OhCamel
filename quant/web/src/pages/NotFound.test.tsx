import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import NotFound from "./NotFound";

describe("NotFound (labels, and back to FRONT)", () => {
  const html = renderToStaticMarkup(
    <MemoryRouter>
      <NotFound />
    </MemoryRouter>,
  );
  it("says 404 · NO PAGE, not a sentence", () => {
    expect(html).toContain("404 · NO PAGE");
    expect(html).not.toMatch(/There&#x27;s no page|Try the search/);
  });
  it("names the command keys and links FRONT", () => {
    expect(html).toContain("/ · ⌘K · TICKER OR PAGE");
    expect(html).toMatch(/href="\/"[^>]*>FRONT</);
  });
});
