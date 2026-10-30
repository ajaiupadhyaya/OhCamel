/**
 * The machine's colours, read from the `--mx-*` tokens in styles/tokens.css, so page code holds no raw hex.
 *
 * The scene works in display space: a token's numbers go into the shaders as they are, and the renderer's output
 * does not convert them (`renderer.outputColorSpace` in scene.ts). The brightness levels of spec section 6.6, 0.07
 * and 0.014 and the additive blending over them, were tuned that way in the approved prototype; in linear space
 * they would read several times brighter. So a colour here is taken as linear on purpose: `Color` then leaves the
 * numbers alone, and a token on the page is the same colour on the canvas.
 */
import { Color, LinearSRGBColorSpace } from "three";

export type PaletteKey = "ground" | "ink" | "bright" | "dim" | "line" | "structure" | "reads" | "feeds" | "fail";
export type Palette = Record<PaletteKey, Color>;

const KEYS: readonly PaletteKey[] = ["ground", "ink", "bright", "dim", "line", "structure", "reads", "feeds", "fail"];

/** The palette as `el` sees it. */
export function readPalette(el: Element): Palette {
  const style = getComputedStyle(el);
  const read = (key: PaletteKey): Color => new Color().setStyle(style.getPropertyValue(`--mx-${key}`).trim(), LinearSRGBColorSpace);
  return Object.fromEntries(KEYS.map((key) => [key, read(key)])) as Palette;
}
