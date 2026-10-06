/**
 * The Flight Deck's one page key: `d` toggles the right drawer (limits editor, marks,
 * inspection). Pure so keys.test.ts can run it without a DOM. A key the shell already
 * consumed (its `g d` chord to reach this page) arrives with defaultPrevented and is ignored.
 */
import { isTypingTarget } from "../../shell/hotkeys";

export interface KeyLike {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  defaultPrevented?: boolean;
  target?: { tagName?: string; isContentEditable?: boolean } | null;
}

export function drawerKey(e: KeyLike): boolean {
  if (e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return false;
  if (isTypingTarget(e.target ?? null)) return false;
  return e.key === "d" || e.key === "D";
}
