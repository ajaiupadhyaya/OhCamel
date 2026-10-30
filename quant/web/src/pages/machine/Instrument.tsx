/**
 * The machine as an instrument: the canvas fills the screen and the panels float over it. Select one thing and its web
 * lights: what it reads, what it feeds, in plain words. On a phone the panels are one bottom sheet (machine.css).
 *
 * The address is the view: `sel`, `tab` and `step` are read from it on every render and written to it on every change,
 * so a view is always a deep link and nothing here can drift from it. The camera is the one thing the address does not
 * hold; `go` moves it, because a flight belongs to the act of going somewhere, not to where the page is. An address the
 * page did not write (the wordmark, `FRONT`, back and forward) is an arrival, and moves it too. A new scene puts the
 * camera back on the view as it stands and changes nothing else.
 *
 * Per-frame work never reaches this component: labels and the tooltip take their updates in Overlay.tsx, and the replay
 * runs in useReplay.ts. This module and what it imports are the only part of the page that needs three.js.
 */
import { useEffect, useReducer, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { isTypingTarget } from "../../shell/hotkeys";
import { FocusPanel } from "./FocusPanel";
import { Legend } from "./Legend";
import { Labels, Tooltip } from "./Overlay";
import type { FocusCam } from "./scene";
import type { PlacedLabel } from "./scene-labels";
import { SidePanel } from "./SidePanel";
import { TopBar } from "./TopBar";
import { escapeAction, tourDir } from "./keys";
import { STEPS, stepView } from "./tour";
import type { Run } from "./types";
import { arrival, viewCam, viewSearch } from "./url";
import type { StepView, Tab, View } from "./url";
import { PHONE, REDUCED_MOTION, SIDEWAYS, useMedia } from "./useMedia";
import { useReplay } from "./useReplay";
import { useScene } from "./useScene";
import type { GraphIndex, WebMode } from "./web";
import { WHEEL_IDLE, wheelStep } from "./wheel";

/** What the panels take from the left and right of the screen, their 14 px margins included, so the machine centres in what is left. */
const FOCUS_ROOM = 394;
const SIDE_ROOM = 424;

export interface InstrumentProps {
  gi: GraphIndex;
  runs: Run[];
  view: View;
  now: Date;
  /** This browser has stopped being able to draw. */
  onLost(): void;
}

export default function Instrument({ gi, runs, view, now, onLost }: InstrumentProps) {
  const nav = useNavigate();
  const [mode, setMode] = useState<WebMode>("web");
  const phone = useMedia(PHONE);
  const sideways = useMedia(SIDEWAYS);
  const reduced = useMedia(REDUCED_MOTION);

  const labels = useRef<((placed: PlacedLabel[]) => void) | null>(null);
  const tip = useRef<((i: number | null, x: number, y: number) => void) | null>(null);
  const { canvas, scene } = useScene(
    gi,
    {
      onHover: (i, x, y) => tip.current?.(i, x, y),
      onPick: (i) => select(i === null ? null : gi.out.nodes[i].id),
      onLabels: (placed) => labels.current?.(placed),
      onLost,
    },
    reduced,
  );
  const replay = useReplay(scene, gi, runs, reduced);

  const indexOf = (id: string | null): number | null => (id === null ? null : gi.byId.get(id) ?? null);
  const selected = indexOf(view.sel);

  // The view as the page last asked for it, ahead of the router's render: two keys in a row must both count.
  const asked = useRef(view);

  const go = (next: View, cam: FocusCam): void => {
    asked.current = next;
    const i = indexOf(next.sel);
    scene?.setWeb(i, mode);
    scene?.focus(i, cam);
    const search = viewSearch(next);
    if (search !== window.location.search) nav({ search }, { replace: true });
  };

  /** Select a node, which opens its reading; null deselects and stays where it is. */
  function select(id: string | null): void {
    replay.stop();
    const v = asked.current;
    if (id !== null) go({ ...v, sel: id, tab: "read" }, "node");
    else if (v.sel !== null) go({ ...v, sel: null }, "keep");
  }

  /**
   * A selection made inside a panel. The control that made it can go with the old selection, and focus would fall to
   * the body, so it follows to the new heading.
   */
  const heading = useRef<HTMLHeadingElement>(null);
  const hopping = useRef(false);
  const hop = (id: string): void => {
    if (id === asked.current.sel && heading.current) heading.current.focus({ preventScroll: true });
    else hopping.current = true;
    select(id);
  };

  const stepAt = (step: number): StepView => stepView(gi, step, { runs, now });

  const goStep = (step: number): void => {
    replay.stop();
    const k = Math.max(1, Math.min(STEPS.length, step));
    const s = stepAt(k);
    go({ ...asked.current, sel: s.sel, tab: "tour", step: k }, s.cam);
  };
  const stepBy = (dir: number): void => {
    const { step } = asked.current;
    if (step + dir >= 1 && step + dir <= STEPS.length) goStep(step + dir);
  };

  /** Asked of a tab on the phone's sheet: bring its pane up, past whatever sits above the tabs. */
  const paneWanted = useRef(false);
  const [paneAsked, askPane] = useReducer((n: number) => n + 1, 0);

  const setTab = (tab: Tab): void => {
    paneWanted.current = true;
    askPane();
    if (tab === "tour") goStep(asked.current.step);
    else {
      if (tab !== "day") replay.stop();
      go({ ...asked.current, tab }, "keep");
    }
  };

  const startReplay = (): void => {
    go({ ...asked.current, sel: null, tab: "day" }, "clock");
    replay.start();
  };

  /** The address changed, or the page has just loaded: go where it says. */
  const arrive = (): void => {
    replay.stop();
    const v = asked.current;
    const a = arrival(v, stepAt(v.step));
    go(a.view, a.cam);
  };

  /** A new scene: the same view, with the camera back on it. A rebuild never moves the page. */
  const restore = (): void => go(view, viewCam(view, stepAt(view.step)));

  const onKey = (e: KeyboardEvent): void => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const typing = isTypingTarget(e.target as HTMLElement | null);
    if (e.key === "Escape") {
      const overlay = document.querySelector(".oc-cmd, .oc-keymap, .mx-pages[open]") !== null;
      const action = escapeAction({ overlay, handled: e.defaultPrevented, typing, replaying: replay.playing, selected: asked.current.sel !== null });
      if (action === "stop-replay") replay.stop();
      else if (action === "deselect") select(null);
      return;
    }
    const dir = tourDir(e.key);
    if (dir === 0 || e.defaultPrevented || typing || asked.current.tab !== "tour") return;
    e.preventDefault();
    stepBy(dir);
  };

  const wheelState = useRef(WHEEL_IDLE);
  const onWheel = (e: WheelEvent): void => {
    e.preventDefault();
    if (asked.current.tab !== "tour") {
      scene?.zoom(e.deltaY);
      return;
    }
    const { state, dir } = wheelStep(wheelState.current, e.deltaY, e.timeStamp);
    wheelState.current = state;
    if (dir !== 0) stepBy(dir);
  };

  // Listeners and the effects below read the latest of these.
  const latest = useRef({ arrive, restore, onKey, onWheel });
  useEffect(() => {
    latest.current = { arrive, restore, onKey, onWheel };
  });

  // Every address the page writes goes through `go` first, so one it did not ask for came from outside it.
  useEffect(() => {
    const outside = viewSearch(view) !== viewSearch(asked.current);
    asked.current = view;
    if (outside) latest.current.arrive();
  }, [view]);

  useEffect(() => {
    if (!hopping.current) return;
    hopping.current = false;
    heading.current?.focus({ preventScroll: true });
  }, [view.sel, view.tab]);

  // What the address says is lit, however it came to say it.
  useEffect(() => {
    scene?.setWeb(selected, mode);
  }, [scene, selected, mode]);
  useEffect(() => {
    scene?.setRuns(runs);
  }, [scene, runs]);

  // The sheet is the phone's view offset: the machine centres in what it leaves, above it or, on a phone on its side, beside it.
  const sheet = useRef<HTMLDivElement>(null);
  const [sheetSized, sized] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    const el = sheet.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const seen = new ResizeObserver(() => sized());
    seen.observe(el);
    return () => seen.disconnect();
  }, []);
  useEffect(() => {
    const el = sheet.current;
    if (!phone || !el) scene?.setViewOffset(selected === null ? 0 : FOCUS_ROOM, SIDE_ROOM, 0);
    else if (sideways) scene?.setViewOffset(0, el.offsetWidth, 0);
    else scene?.setViewOffset(0, 0, el.offsetHeight);
  }, [scene, selected, phone, sideways, sheetSized]);

  // On the sheet a new selection or tab starts at its own top; the tour keeps its place, because it scrolls itself.
  useEffect(() => {
    if (phone && view.tab !== "tour") sheet.current?.scrollTo({ top: 0 });
  }, [phone, view.sel, view.tab]);
  useEffect(() => {
    if (!paneWanted.current || asked.current.tab !== view.tab) return;
    paneWanted.current = false;
    const above = sheet.current?.querySelector<HTMLElement>(".mx-focus");
    if (phone && above) sheet.current?.scrollTo({ top: above.offsetHeight });
  }, [phone, view.tab, paneAsked]);

  const placed = useRef(false);
  useEffect(() => {
    if (!scene) return;
    if (placed.current) latest.current.restore();
    else latest.current.arrive();
    placed.current = true;
  }, [scene]);

  useEffect(() => {
    const key = (e: KeyboardEvent): void => latest.current.onKey(e);
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  useEffect(() => {
    const el = canvas.current;
    const wheel = (e: WheelEvent): void => latest.current.onWheel(e);
    el?.addEventListener("wheel", wheel, { passive: false }); // the wheel is the page's: it must not scroll or zoom the browser
    return () => el?.removeEventListener("wheel", wheel);
  }, [canvas]);

  return (
    <>
      <canvas ref={canvas} className="mx-canvas" aria-hidden="true" />
      <Labels sink={labels} />
      <TopBar gi={gi} now={now} view={view} mode={mode} onMode={setMode} onSelect={select} drawable />
      <div ref={sheet} className="mx-panels">
        {selected !== null && !(phone && view.tab === "tour") && (
          <FocusPanel gi={gi} i={selected} mode={mode} runs={runs} now={now} heading={heading} onSelect={hop} onClose={() => select(null)} />
        )}
        <SidePanel gi={gi} view={view} runs={runs} now={now} replay={replay} onTab={setTab} onStep={goStep} onSelect={hop} onReplay={startReplay} onStopReplay={replay.stop} phone={phone} />
      </div>
      <Legend />
      <Tooltip gi={gi} sink={tip} />
    </>
  );
}
