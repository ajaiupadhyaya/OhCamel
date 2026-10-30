/**
 * The Machine (/): OhCamel's real dependency graph as an instrument (spec section 6). The canvas fills the screen and
 * the panels float over it. Select one thing and its web lights: what it reads, what it feeds, in plain words.
 *
 * The address is the view: `sel`, `tab` and `step` are read from it on every render and written to it on every change,
 * so a view is always a deep link and nothing here can drift from it. The camera is the one thing the address does not
 * hold; `go` moves it, because a flight belongs to the act of going somewhere, not to where the page is. An address the
 * page did not write (the wordmark, `FRONT`, back and forward) is an arrival, and moves it too. A new scene puts the
 * camera back on the view as it stands and changes nothing else.
 *
 * Per-frame work never reaches this component: labels and the tooltip take their updates in Overlay.tsx, and the replay
 * runs in useReplay.ts. Page-local code lives in ./machine/ (CSS prefix `mx-`).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { isTypingTarget } from "../shell/hotkeys";
import { FocusPanel } from "./machine/FocusPanel";
import { Legend } from "./machine/Legend";
import { Labels, Tooltip } from "./machine/Overlay";
import { webglAvailable } from "./machine/scene";
import type { FocusCam } from "./machine/scene";
import type { PlacedLabel } from "./machine/scene-labels";
import { SidePanel } from "./machine/SidePanel";
import { TopBar } from "./machine/TopBar";
import { escapeAction, tourDir } from "./machine/keys";
import { STEPS } from "./machine/tour";
import type { Run } from "./machine/types";
import { arrival, parseView, viewCam, viewSearch } from "./machine/url";
import type { StepView, Tab, View } from "./machine/url";
import { useMachine } from "./machine/useMachine";
import { useReplay } from "./machine/useReplay";
import { useScene } from "./machine/useScene";
import { indexGraph } from "./machine/web";
import type { GraphIndex, WebMode } from "./machine/web";
import { WHEEL_IDLE, wheelStep } from "./machine/wheel";
import "./machine/machine.css";

/** What the panels take from the left and right of the screen, their 14 px margins included, so the machine centres in what is left. */
const FOCUS_ROOM = 394;
const SIDE_ROOM = 424;
/** How often the page's clock moves: readings and the day list say "the last day", and a sitting page must not go stale. */
const NOW_EVERY_MS = 30_000;

function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), NOW_EVERY_MS);
    return () => clearInterval(t);
  }, []);
  return now;
}

export default function Machine() {
  const { data, error, runs, retry } = useMachine();
  const gi = useMemo(() => (data ? indexGraph(data) : null), [data]);
  const [webgl] = useState(webglAvailable);
  useEffect(() => {
    document.title = "The machine · OhCamel Quant";
  }, []);

  return (
    <div className="mx-root">
      <h1 className="mx-vh">The machine</h1>
      {gi && webgl && <Instrument gi={gi} runs={runs} />}
      {gi && !webgl && <p className="mx-wait">This browser cannot draw the machine: WebGL is off or unavailable.</p>}
      {!gi && !error && <p className="mx-wait">Reading the machine…</p>}
      {Boolean(error) && (
        <p className={gi ? "mx-wait mx-wait-low" : "mx-wait"} role="alert">
          {gi ? "The latest read of the machine failed, so this is the last one that worked: " : "The machine could not be read: "}
          {error instanceof Error ? error.message : String(error)}{" "}
          <button type="button" onClick={retry}>
            retry
          </button>
        </p>
      )}
    </div>
  );
}

function Instrument({ gi, runs }: { gi: GraphIndex; runs: Run[] }) {
  const nav = useNavigate();
  const { search: address } = useLocation();
  const view = useMemo(() => parseView(address, (id) => gi.byId.has(id)), [address, gi]);
  const [mode, setMode] = useState<WebMode>("web");
  const now = useNow();

  const labels = useRef<((placed: PlacedLabel[]) => void) | null>(null);
  const tip = useRef<((i: number | null, x: number, y: number) => void) | null>(null);
  const { canvas, scene } = useScene(gi, {
    onHover: (i, x, y) => tip.current?.(i, x, y),
    onPick: (i) => select(i === null ? null : gi.out.nodes[i].id),
    onLabels: (placed) => labels.current?.(placed),
  });
  const replay = useReplay(scene, gi, runs);

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
    if (id === asked.current.sel) heading.current?.focus({ preventScroll: true });
    else hopping.current = true;
    select(id);
  };

  const stepView = (step: number): StepView => {
    const s = STEPS[step - 1];
    const i = s.sel(gi, { runs, now });
    return { sel: i === null ? null : gi.out.nodes[i].id, cam: s.cam };
  };

  const goStep = (step: number): void => {
    replay.stop();
    const k = Math.max(1, Math.min(STEPS.length, step));
    const s = stepView(k);
    go({ sel: s.sel, tab: "tour", step: k }, s.cam);
  };
  const stepBy = (dir: number): void => {
    const { step } = asked.current;
    if (step + dir >= 1 && step + dir <= STEPS.length) goStep(step + dir);
  };

  const setTab = (tab: Tab): void => {
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
    const a = arrival(v, stepView(v.step));
    go(a.view, a.cam);
  };

  /** A new scene: the same view, with the camera back on it. A rebuild never moves the page. */
  const restore = (): void => go(view, viewCam(view, stepView(view.step)));

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
  }, [view.sel]);

  // What the address says is lit, however it came to say it.
  useEffect(() => {
    scene?.setWeb(selected, mode);
  }, [scene, selected, mode]);
  // `now` too: under reduced motion the hand moves only when this is called.
  useEffect(() => {
    scene?.setRuns(runs);
  }, [scene, runs, now]);
  useEffect(() => {
    scene?.setViewOffset(selected === null ? 0 : FOCUS_ROOM, SIDE_ROOM, 0);
  }, [scene, selected]);
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
      <TopBar gi={gi} now={now} mode={mode} onMode={setMode} onSelect={select} />
      {selected !== null && (
        <FocusPanel gi={gi} i={selected} mode={mode} runs={runs} now={now} heading={heading} onSelect={hop} onClose={() => select(null)} />
      )}
      <SidePanel gi={gi} view={view} runs={runs} now={now} replay={replay} onTab={setTab} onStep={goStep} onSelect={hop} onReplay={startReplay} onStopReplay={replay.stop} />
      <Legend />
      <Tooltip gi={gi} sink={tip} />
    </>
  );
}
