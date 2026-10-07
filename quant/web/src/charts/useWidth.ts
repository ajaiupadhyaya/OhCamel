import { useLayoutEffect, useRef, useState } from "react";

/** A container ref and its content width, kept current with a ResizeObserver (SVG charts). */
export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node) return;
    setWidth(node.clientWidth);
    const ro = new ResizeObserver((e) => setWidth(Math.round(e[0]?.contentRect.width ?? 0)));
    ro.observe(node);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}
