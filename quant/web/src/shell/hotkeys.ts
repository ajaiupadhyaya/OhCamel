/**
 * Global keys: `/` and Cmd/Ctrl-K open the command line, `?` opens the key map, and
 * `g` then a letter (within CHORD_MS) jumps to a section. The chord is a small pure state
 * machine (chordStep) so it can be tested without a DOM; useHotkeys wires it to window.
 * Plain keys are ignored while the user is typing in a field.
 */
import { useEffect, useRef } from "react";

export const CHORD_MS = 1000;

/** `g` + key → path. The key map overlay lists these in this order. */
export const CHORDS: readonly { key: string; path: string; label: string }[] = [
  { key: "m", path: "/markets", label: "MARKETS" },
  { key: "r", path: "/risk", label: "RISK" },
  { key: "p", path: "/portfolio", label: "PORTFOLIO" },
  { key: "d", path: "/deck", label: "FLIGHT DECK" },
  { key: "s", path: "/system", label: "SYSTEM" },
];

export interface ChordState {
  /** Time (ms) the leading `g` was pressed, or null when no chord is pending. */
  since: number | null;
}
export const IDLE: ChordState = { since: null };

export function chordStep(state: ChordState, key: string, now: number): { state: ChordState; path: string | null; consumed: boolean } {
  if (state.since !== null && now - state.since <= CHORD_MS) {
    const hit = CHORDS.find((c) => c.key === key);
    if (hit) return { state: IDLE, path: hit.path, consumed: true };
    if (key === "g") return { state: { since: now }, path: null, consumed: true };
    return { state: IDLE, path: null, consumed: false };
  }
  if (key === "g") return { state: { since: now }, path: null, consumed: true };
  return { state: IDLE, path: null, consumed: false };
}

export function isTypingTarget(el: { tagName?: string; isContentEditable?: boolean } | null | undefined): boolean {
  if (!el) return false;
  return /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName ?? "") || el.isContentEditable === true;
}

export interface HotkeyHandlers {
  onCommand: () => void;
  onHelp: () => void;
  onNavigate: (path: string) => void;
}

export function useHotkeys(handlers: HotkeyHandlers): void {
  const ref = useRef(handlers);
  useEffect(() => {
    ref.current = handlers;
  });
  useEffect(() => {
    let chord: ChordState = IDLE;
    const on = (e: KeyboardEvent) => {
      const h = ref.current;
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        chord = IDLE;
        h.onCommand();
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
      if (isTypingTarget(e.target as HTMLElement | null)) return;
      if (e.key === "/") {
        e.preventDefault();
        chord = IDLE;
        h.onCommand();
        return;
      }
      if (e.key === "?") {
        e.preventDefault();
        chord = IDLE;
        h.onHelp();
        return;
      }
      const r = chordStep(chord, e.key, e.timeStamp || performance.now());
      chord = r.state;
      if (r.consumed) e.preventDefault();
      if (r.path) h.onNavigate(r.path);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
}
