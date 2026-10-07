/**
 * The content-box size of an element, kept current with a ResizeObserver (Lane F2: the
 * instruments draw in the pixels they are given). The element's size must come from CSS,
 * never from what is drawn inside it, or the two would chase each other. A callback ref, so
 * an element that mounts after the first render (a tape that fills in later) is still measured.
 */
import { useCallback, useLayoutEffect, useState } from "react";

export interface Box {
  width: number;
  height: number;
}

export function useBox<T extends Element>(fallback: Box): [(el: T | null) => void, Box] {
  const [el, setEl] = useState<T | null>(null);
  const [box, setBox] = useState<Box>(fallback);
  const ref = useCallback((node: T | null) => setEl(node), []);
  useLayoutEffect(() => {
    if (!el) return;
    const set = (width: number, height: number) =>
      setBox((b) => (Math.abs(b.width - width) < 0.5 && Math.abs(b.height - height) < 0.5 ? b : { width, height }));
    const r = el.getBoundingClientRect();
    set(r.width, r.height);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const c = entries[entries.length - 1]?.contentRect;
      if (c) set(c.width, c.height);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [ref, box];
}
