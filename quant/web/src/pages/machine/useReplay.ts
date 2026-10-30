/**
 * The replay of the last day: a hand sweeps it in `REPLAY_S`, and the last job to have started, running or not, stays lit (`replayHold`).
 * The sweep runs off React: each frame goes straight to the scene and the clock's text, and the page re-renders only
 * when the held run changes, to light its row. `now` is fixed when the sweep starts, so the day does not slide under it.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { REPLAY_S, replayFrame, replayHold } from "./day";
import { nyTime, nyWeekday } from "./fmt";
import type { SceneApi } from "./scene";
import type { Run } from "./types";
import type { GraphIndex } from "./web";

const jobOf = (gi: GraphIndex, r: Run): number | undefined => gi.byId.get(`j:${r.kind}`);

/** The hand's time, as the Day tab shows it: "Wed 6:30 PM ET". */
function clockText(at: Date): string {
  const iso = at.toISOString();
  return `${nyWeekday(iso).slice(0, 3)} ${nyTime(iso)} ET`;
}

export function useReplay(scene: SceneApi | null, gi: GraphIndex, runs: Run[]) {
  const [playing, setPlaying] = useState(false);
  const [held, setHeld] = useState<string | null>(null);
  /** Where the sweep writes its time. */
  const clock = useRef<HTMLSpanElement>(null);
  const frame = useRef(0);
  const latest = useRef({ scene, gi, runs });
  useEffect(() => {
    latest.current = { scene, gi, runs };
  });

  const stop = useCallback(() => {
    if (frame.current === 0) return;
    cancelAnimationFrame(frame.current);
    frame.current = 0;
    latest.current.scene?.setReplay(null, []);
    setPlaying(false);
    setHeld(null);
  }, []);

  const start = useCallback(() => {
    stop();
    const { scene: s, gi: graph, runs: all } = latest.current;
    if (!s) return;
    const now = new Date();
    const known = all.filter((r) => jobOf(graph, r) !== undefined); // a run with no node has nothing to light
    const began = performance.now();
    let lit: string | null = null;
    const tick = (): void => {
      const frac = (performance.now() - began) / (REPLAY_S * 1000);
      if (frac >= 1) {
        stop();
        return;
      }
      frame.current = requestAnimationFrame(tick);
      const run = replayHold(known, now, frac);
      s.setReplay(frac, run ? [jobOf(graph, run)!] : []);
      if ((run?.id ?? null) !== lit) {
        lit = run?.id ?? null;
        setHeld(lit);
      }
      if (clock.current) clock.current.textContent = clockText(replayFrame([], now, frac).at);
    };
    setPlaying(true);
    frame.current = requestAnimationFrame(tick);
  }, [stop]);

  // a new scene has no replay in it, and leaving the page ends the sweep
  useEffect(() => stop, [scene, stop]);

  return { playing, held, clock, start, stop };
}
