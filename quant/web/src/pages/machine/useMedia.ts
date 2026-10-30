/** A CSS media query as live React state, and the three the machine asks. machine.css holds the same widths. */
import { useCallback, useSyncExternalStore } from "react";

/** The phone: the panels are one bottom sheet, and the machine stands in the rest of the screen. */
export const PHONE = "(max-width: 860px)";
/** A phone on its side: the sheet stands at the right. Not `orientation`: an on-screen keyboard makes a phone upright look landscape. */
export const SIDEWAYS = "(max-width: 860px) and (min-width: 560px) and (max-height: 500px)";
export const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

export function useMedia(query: string): boolean {
  const subscribe = useCallback(
    (changed: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", changed);
      return () => list.removeEventListener("change", changed);
    },
    [query],
  );
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches, () => false);
}
