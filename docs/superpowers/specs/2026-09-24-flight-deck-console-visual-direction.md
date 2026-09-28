# The Flight Deck console — visual direction (pre-spec)

*2026-09-24. Owner direction, recorded so Lane F's F0 task starts from it. This
is the written-back understanding the brainstorming step asks for, confirmed by
the owner's four decisions below; F0 writes the design spec from it.*

## What the owner said

Six reference frames were supplied as visual inspiration for the Flight Deck
tab, with this verdict on the current page: "Your current renditions aren't
real enough, they look fake and vibecoded with the static generic assets used
to design it. Let's thoroughly be inspired and thoughtfully design so that
there's an undoubtable 'holy shit' type feel when someone looks at it."

Read against the compute program plan: Task 0.1 lands the page functional as it
stands; Lane F (`F0` plan, `F1`–`F4`) rebuilds its presentation on the Terminal
design system (D1–D3) with `hostd` (0.4) feeding the lamp panel. The pure logic
in `quant/web/src/pages/deck/{scope,radar,model,limits,segments}.ts` is kept.
This note binds what "rebuilds its presentation" must mean.

## The six references, and what each one actually does

1. **Falcon sensor scope (yellow vector, round CRT, red cabin).** A polar
   display: radial spokes and concentric rings in one amber-yellow phosphor,
   green tick marks at the cardinal points, three lines of uppercase readout
   text *drawn in the same phosphor* ("ENV/CON 4490 / STA/CON 4590 / CONC…").
   The screen sits in a deep black bezel, and the whole cabin is lit red, so
   every surface around the screen carries a red ambient wash. Nothing on the
   screen is decorative: every stroke is a bearing or a number.
2. **Discovery pod console (2001).** A wall of small screens, each one tint:
   sky blue, cyan ("MEM"), magenta, white. Between them, dense grids of backlit
   rectangular pushbuttons in yellow, white and amber, and square lamps in red,
   cyan and green. Each screen has a three-letter header and a self-contained
   readout. The composition is asymmetric and packed; black separates
   everything; there are no gradients on surfaces, only lit and unlit.
3. **Death Star sphere, white dot-matrix CRT.** A wireframe planet drawn as
   *dots* (a raster sampling of a vector), white on black, in a chunky black
   frame with rounded inner corners, set into a matte dark-grey panel with
   physical toggle switches and a column of red/blue/white square lamps with
   tiny printed legends. Monochrome, low resolution, unmistakably a machine.
4. **TIE targeting screen.** A solid saturated red field with yellow lines: a
   perspective grid and a circle, plus two small yellow chevron cursors. The
   bezel is a rounded rectangle with a thick lip. The red field *is* the alarm
   state: the whole screen is the warning, not a badge on it.
5. **X-wing scanner (angular screen).** Orange-white raster imagery like a
   terrain contour map, with vector overlays in pale green and yellow
   (concentric arcs, a striped disc), small cyan diamond markers, and an amber
   seven-segment timer ("00:27:1"). The screen outline is a chamfered polygon,
   not a rectangle. Raster and vector layers coexist, each in its own hue.
6. **X-wing targeting computer.** THE reference for the limits instrument:
   yellow perspective lines converging to a vanishing point, two red vertical
   rails, a rounded bezel, and directly beneath it a red seven-segment counter
   ("025960"). To the sides, square backlit icon buttons (white, red, orange)
   and small round indicator lamps (green, amber, red, cyan).

## The principles the references share

These are what makes them read as real, and what the current page lacks.

- **Everything on a screen is a stroke on a phosphor.** One hue per screen,
  crisp core line over a soft same-hue bloom, a faint raster (scanline) texture,
  a vignette toward the bezel, slight corner rounding and barrel curvature, and
  phosphor persistence: the previous frame decays over a few hundred
  milliseconds instead of vanishing. Text on a screen is drawn the same way,
  uppercase, wide, in the screen's hue. There are no white UI labels floating
  over a "screen".
- **The alarm is the screen.** A breached limit does not add a red badge; the
  targeting computer's field goes solid red with yellow lines (ref 4) until the
  breach clears. Severity is a change of phosphor, not an icon.
- **Numbers live in seven-segment faces**, red or amber, below or beside the
  screen they belong to (refs 5 and 6), never in body type on the bezel.
- **Bezels are physical.** A deep black rounded-rectangle frame with an inner
  lip, one-pixel top specular, inset into a matte near-black panel with a fine
  noise grain. No shadowed cards, no glass, no gradients on surfaces. The one
  permitted gradient is the cabin's ambient key light: a faint red radial wash
  across the whole console (ref 1) that every bezel and lamp reflects.
- **Lamps, not chips.** Feeds, jobs and CPU are backlit square pushbuttons in
  a dense grid (ref 2): lit = a saturated fill (cyan, amber, red, green, white)
  with an inner glow and a printed legend; unlit = dark inset with the legend
  at ten percent. A lamp's state is a data reading, and every lamp is labelled.
- **Composition is a wall, not a grid of equal cards.** Screens of different
  sizes and shapes (a round scope, a chamfered wide scanner, a square
  targeting screen), packed asymmetrically with the lamp fields between them.
- **Motion is instrument motion.** A sweep with a decay trail on the scope; the
  trench's cross-lines advancing toward the viewer; chevron cursors moving with
  the reading; a blinking text cursor. All of it honours
  `prefers-reduced-motion` by freezing on the current frame.
- **Nothing is decorative.** Every ring, spoke, gate, rail, dot, lamp and digit
  maps to a number from the reading. If it does not, it is cut. That is the
  Global Constraints' honesty rule applied to pixels.

## What this rules out (the "vibecoded" tells)

Generic HUD corner brackets and reticles; stock sci-fi icon fonts and Orbitron
everywhere; rounded 14 px shadowed cards; glassmorphism and blur; gradients on
panels; neon outlines on every element; a uniform grid of identical widgets;
white body text over dark "screens"; static PNG or SVG props that do not move
with the data; anything that would look the same with the data removed.

## How it maps onto the instruments the plan already names

| Instrument (plan I.5) | Reference | Rendering |
|---|---|---|
| Targeting computer (limits: a gate per limit at a depth set by headroom, breaches as rails) | 6, then 4 | Amber-yellow phosphor trench with the gates as cross-frames; the red rails on a breach; the whole field goes red-with-yellow-lines while any limit is breached; seven-segment red readout of the binding limit's headroom beneath |
| Radar (risk: positions by contribution and sector) | 1, then 3 | Round green or amber polar scope with radial sector spokes and rings, a rotating sweep with persistence; positions as blips whose radius is their risk contribution; the reading's three lines of text drawn on the phosphor; an optional dot-matrix mode for the book as a whole |
| Lamp panel (feeds, jobs, CPU) | 2 | A backlit pushbutton field; one lamp per feed and per job kind; CPU, steal and memory from `hostd` as a column of amber bar lamps; a "MEM"-style three-letter header per group |
| Session tape | 5 | The chamfered wide scanner: the session's P&L and VaR as orange raster contour bands, the current reading as vector arcs, session clock as an amber seven-segment timer, cyan diamond markers at limit events |
| Limits editor and marks | — | A side drawer, as I.5 says; conventional Terminal controls, because it is an editor and not an instrument |

## Techniques the F0 plan must specify (so it is buildable, not adjectives)

- Canvas (or SVG with filters) per screen: draw once into an offscreen buffer
  in the phosphor hue; composite as glow (blurred, low alpha) plus core
  (crisp); apply a scanline mask, a vignette and a rounded-corner clip; keep the
  previous buffer at decaying alpha for persistence. One shared `CrtScreen`
  primitive takes `{hue, shape: 'round'|'rect'|'chamfer', persistence}` and the
  instrument draws into it.
- A `Lamp` primitive: `{legend, hue, lit, blink?}` with the inset and glow
  states; a `LampField` lays them out in the reference's density.
- The existing `SevenSeg.tsx` becomes the numeric face everywhere on the
  console; body type never appears inside a bezel.
- The console tab is full-bleed with no `Page`/`Panel` chrome (I.5), sits on
  the Terminal tokens (D1) for the panel greys, and adds its own phosphor hues
  (amber, green, red-field, cyan, white) as console-only tokens.
- Performance: the sweep and persistence run at the display's refresh rate on
  one `requestAnimationFrame` per console, not one per instrument; a hidden tab
  stops drawing; 380 px stacks the screens vertically with the lamp field
  collapsed into a two-row strip (Global Constraint 14).
- Verification: screenshot tests at 1280 and 380 px in both themes, and a
  "data removed" test: with an empty reading, the screens show only the
  static geometry and a `NO READING` phosphor line, proving nothing decorative
  is left.

## Owner decisions (2026-09-24, answered the same day)

1. **Per-screen hues.** Amber-yellow targeting computer, green scope, the
   red-with-yellow alarm field, cyan markers, white dot-matrix where used; the
   table above stands.
2. **The red cabin wash stays, in both themes.** The light theme shows a lit
   room around a dark instrument; the console itself is never re-tinted light.
3. **Instrument look only.** Bloom, raster, persistence and curvature; no
   flicker, no misconvergence, no fake glass reflections, no film-camera
   artefacts.
4. **No sound.**

These four are binding on F0 and on every F task; a change is a `plan:`
commit that amends this note first.

## Where this lands in the program

Lane F depends on D1–D3 (tokens, primitives, panels) and 0.4 (`hostd`). The
plan's `F0` writes the lane's bite-sized plan; it must cite this note, and F1
starts with the `CrtScreen` and `Lamp` primitives before any instrument is
redrawn. If the owner wants the console sooner than the dependency graph
allows, F0 can be written after G0 and F1 can start on D1's tokens alone,
with `hostd`'s lamps arriving when 0.4 deploys.
