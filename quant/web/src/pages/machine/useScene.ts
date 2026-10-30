/**
 * The scene's life in React: made once on the canvas, and made again only when a newer read of the machine changes
 * what it draws (`drawnShape`); panels take every read, the scene only those. The handlers may change on every render;
 * the scene always calls the latest.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { nyHour } from "./fmt";
import { readPalette } from "./palette";
import { createScene } from "./scene";
import type { SceneApi, SceneHandlers } from "./scene";
import { drawnShape } from "./web";
import type { GraphIndex } from "./web";

export function useScene(gi: GraphIndex, handlers: SceneHandlers) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [scene, setScene] = useState<SceneApi | null>(null);
  const latest = useRef({ gi, handlers });
  useEffect(() => {
    latest.current = { gi, handlers };
  });

  const shape = useMemo(() => drawnShape(gi.out), [gi]);
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const made = createScene(
      el,
      gi,
      {
        palette: readPalette(el),
        reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
        nowNY: () => nyHour(new Date()),
      },
      {
        onHover: (i, x, y) => latest.current.handlers.onHover(i, x, y),
        onPick: (i) => latest.current.handlers.onPick(i),
        onLabels: (placed) => latest.current.handlers.onLabels(placed),
      },
    );
    setScene(made);
    return () => {
      made.dispose();
      setScene(null);
    };
    // the shape stands for the graph here: a read with the same shape keeps this scene
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape]);

  return { canvas, scene };
}
