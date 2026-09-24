# Figure 2 — the nearest limit, as a targeting computer

*2026-09-24. Approved in conversation by the owner; built on `feature/scope`.
Outside the finish plan (`docs/superpowers/plans/2026-09-19-final-completion.md`),
which it does not change: no route, no engine node and no published count moves.*

## What it is

A second figure on the Desk page, after Figure 1, drawn in the manner of the
targeting computer in *Star Wars* (1977) and the cockpit panels around it:
amber vector lines on a dark screen, a perspective tunnel, a segmented
readout, square lamps. It shows the book's limits and how much room each has
left. It is an instrument set into the page, framed as a screen; nothing
outside its bezel changes style.

It reads only what every frame already carries — `s.limits` (`name`, `unit`,
`excess`, `breached`, `utilisation`) and `s.unevaluated` — and the stale
closure `OhCamelShared.staleNode`. No backend change.

## The mapping

- **Every limit ahead is a gate.** Depth `d = clamp(1 − utilisation, 0, 1)`;
  on-screen scale `1 / (1 + 5d)`. An idle limit is a small gate by the
  vanishing point; a full one is at the screen's edge. Perspective compresses
  depth as the film's did, so the tunnel is evocative, not a ruler: the exact
  figures are in the lamps.
- **The target** is the gate ahead with the highest utilisation (ties: first
  by name, so it cannot flicker). It is drawn brighter and is the only gate
  labelled in the tunnel.
- **The readout** is the target's room to spare in its own unit: money as a
  zero-padded six-digit dollar figure (`023400`), fractions in basis points
  (`0084`, captioned bp). With every limit breached it shows the worst breach's
  excess, in red, captioned "over". With nothing evaluable it shows dashes.
- **A breached limit has passed the screen** and becomes a pair of red rails,
  the first at the edge and each further one a step inward, labelled with its
  name and how far over (`nvda-cap +474%`). At most six are drawn; the rest
  are counted.
- **A limit that cannot be evaluated has no depth**, because there is no
  number to place. All of them share one dashed gate beyond the vanishing
  point, in the page's "unknown" blue, labelled `?`. Never drawn as far away,
  because far away reads as safe.
- **A limit on a stale price is dimmed**, by the ledger's rule
  (`staleNode("limit:" + name)`). A stale target dims the readout and its
  caption says so.
- **The lamps**: one square per limit beside the screen, in the book's order,
  amber ahead, red breached, dashed blue unknown, with the name and exact
  utilisation. They are the precise layer, the screen-reader layer and the
  phone layout.

## Page, files, look

- `web/scope.js` — `layout(limits, unevaluated, isStale)` is pure and returns
  the model (gates, target, readout, rails, unknown, lamps); `draw` renders it.
  Exposed as `window.OhCamelScope`, and as `module.exports` for the test.
- `web/index.html` — the `<figure id="scope">`, and a note beside the design
  essay saying why a film instrument is allowed on a page that rejects
  terminal pastiche: it is framed as a screen and changes nothing around it.
- `web/desk.js` — `render(s)` calls the scope with the frame Figure 1 draws.
- `web/page.css` — one block; the screen's own always-dark tokens, so the
  site's palette is untouched and the screen is dark in both schemes.
- `lib/dune` — `scope.js` catted between `graph.js` and `desk.js`.
- The readout's digits are drawn as seven-segment SVG, because the page loads
  no fonts. No glow, scanlines or noise.
- Wide: screen and lamps side by side, capped near 720 px. Under 600 px the
  lamps drop below the screen.

## Motion

Nothing moves on a timer. When a frame changes a limit's utilisation its gate
glides to the new depth over 250 ms; a limit that newly breaches blinks its
rails three times, then holds (never on the first frame). Under
`prefers-reduced-motion` gates jump and rails do not blink.

## Testing

- `web/test/scope.test.js`, run by `node --test web/test/*.test.js` (`make web-test`,
  and a step in CI's `lint` job, whose runner carries node): depth and scale at
  0, ½, 1 and above 1; target choice and its tie-break; readout digits for
  money and fraction, all-breached and nothing-evaluable; rails ordered and
  capped; unknown limits never given a depth; stale propagation; a
  non-finite utilisation handled.
- `test/test_embedded_assets.ml` gains `id="scope"` and
  `window.OhCamelScope` in the dashboard's order markers (no new test case,
  so no published count moves).
- The page checked on a local demo at 380 px and 1280 px in both schemes.
