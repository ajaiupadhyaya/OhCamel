/**
 * What the machine's own keys do. Pure, no DOM; the page brings the event.
 *
 * Esc closes only the topmost thing. The command line, the key map and the Pages menu take it first, on the way down
 * (a capture listener that stops it), so a real key press never reaches the page while one is open. `overlay` says the
 * same from the page's side, for an Esc sent straight to `window`, where listeners run in the order they were added and
 * the page's would come first. Search takes it next and marks it handled. What is left is the page's: a running replay,
 * then the selection.
 */
export type EscapeAction = "none" | "stop-replay" | "deselect";

export function escapeAction(s: { overlay: boolean; handled: boolean; typing: boolean; replaying: boolean; selected: boolean }): EscapeAction {
  if (s.overlay || s.handled || s.typing) return "none";
  if (s.replaying) return "stop-replay";
  return s.selected ? "deselect" : "none";
}

/** Which way a key moves the tour: 1 forward, -1 back, 0 not at all. */
export function tourDir(key: string): -1 | 0 | 1 {
  if (key === "ArrowDown" || key === "PageDown") return 1;
  return key === "ArrowUp" || key === "PageUp" ? -1 : 0;
}
