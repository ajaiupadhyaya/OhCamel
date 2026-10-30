/**
 * The replay of the last day: a hand sweeps it in `REPLAY_S`, and the last job to have started, running or not, stays lit (`replayHold`).
 * The sweep runs off React: each frame goes straight to the scene and the clock's text, and the page re-renders only
 * when the held run changes, to light its row. `now` is fixed when the sweep starts, so the day does not slide under it.
 * Under reduced motion there is no sweep: the same runs light in turn, one a second (`replayStops`), and the hand cuts.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { REPLAY_S, REPLAY_STEP_MS, replayFrame, replayHold, replayStops } from "./day";
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

export function useReplay(scene: SceneApi | null, gi: GraphIndex, runs: Run[], reducedMotion: boolean) {
  const [playing, setPlaying] = useState(false);
  const [held, setHeld] = useState<string | null>(null);
  /** Where the sweep writes its time. */
  const clock = useRef<HTMLSpanElement>(null);
  /** Ends what is running; null when nothing is. */
  const cancel = useRef<(() => void) | null>(null);
  const latest = useRef({ scene, gi, runs, reducedMotion });
  useEffect(() => {
    latest.current = { scene, gi, runs, reducedMotion };
  });

  const stop = useCallback(() => {
    if (!cancel.current) return;
    cancel.current();
    cancel.current = null;
    latest.current.scene?.setReplay(null, []);
    setPlaying(false);
    setHeld(null);
  }, []);

  const start = useCallback(() => {
    stop();
    const { scene: s, gi: graph, runs: all, reducedMotion: stepped } = latest.current;
    if (!s) return;
    const now = new Date();
    const known = all.filter((r) => jobOf(graph, r) !== undefined); // a run with no node has nothing to light
    let lit: string | null = null;
    const show = (frac: number, run: Run | null): void => {
      s.setReplay(frac, run ? [jobOf(graph, run)!] : []);
      if ((run?.id ?? null) !== lit) {
        lit = run?.id ?? null;
        setHeld(lit);
      }
      if (clock.current) clock.current.textContent = clockText(replayFrame([], now, frac).at);
    };
    setPlaying(true);

    if (stepped) {
      const stops = replayStops(known, now);
      let k = 0;
      let timer = 0;
      const step = (): void => {
        if (k >= stops.length) {
          stop();
          return;
        }
        show(stops[k].frac, stops[k].run);
        k += 1;
      };
      // the first stop waits for a frame, as the sweep does, so the clock's text has somewhere to go
      const first = requestAnimationFrame(() => {
        timer = window.setInterval(step, REPLAY_STEP_MS);
        step();
      });
      cancel.current = () => {
        cancelAnimationFrame(first);
        clearInterval(timer);
      };
      return;
    }

    const began = performance.now();
    let frame = 0;
    const tick = (): void => {
      const frac = (performance.now() - began) / (REPLAY_S * 1000);
      if (frac >= 1) {
        stop();
        return;
      }
      frame = requestAnimationFrame(tick);
      show(frac, replayHold(known, now, frac));
    };
    frame = requestAnimationFrame(tick);
    cancel.current = () => cancelAnimationFrame(frame);
  }, [stop]);

  // a new scene has no replay in it, and leaving the page ends the sweep
  useEffect(() => stop, [scene, stop]);

  return { playing, held, clock, start, stop };
}
