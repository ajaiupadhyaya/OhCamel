/**
 * The machine: OhCamel's dependency graph as a dark instrument (spec sections 6.6 and 6.7). This wires the marks
 * together and runs the frame; the marks are scene-nodes, scene-edges, scene-structure and scene-runs, the flights
 * scene-camera, the labels scene-labels, and the arithmetic scene-geom.
 *
 * What a frame costs: one matrix, a few uniforms, and the labels when the view moved. Edge and node attributes are
 * rewritten only when the lit web changes, the pointer is matched against a projection cached until the camera moves,
 * and nothing here touches the DOM beyond the canvas's cursor. Labels leave as plain data; React renders them as text.
 */
import { LinearSRGBColorSpace, Matrix4, PerspectiveCamera, Scene, Vector3, WebGLRenderer } from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { runningRuns } from "./day";
import { layout } from "./layout";
import type { Palette } from "./palette";
import { createFlight } from "./scene-camera";
import { createEdges } from "./scene-edges";
import { CLOCK, frameFit, nearestNode, nodeShot, OVERVIEW, projectNodes, radiusOf, sameView, TOUCH_REACH, YOURS, zoomDistance } from "./scene-geom";
import type { CentreWeb, Shot, Vec3 } from "./scene-geom";
import { labelBook, placeFrame } from "./scene-labels";
import type { LabelSpec, PlacedLabel } from "./scene-labels";
import { createNodes } from "./scene-nodes";
import { createRunRings } from "./scene-runs";
import type { RunMark } from "./scene-runs";
import { createStructure } from "./scene-structure";
import type { Run } from "./types";
import { webOf } from "./web";
import type { GraphIndex, WebMode } from "./web";

export interface SceneHandlers {
  /** The node under the pointer, or null; `x, y` are the pointer's client coordinates. */
  onHover(i: number | null, x: number, y: number): void;
  /** A click: on a node, or null on empty space. */
  onPick(i: number | null): void;
  /**
   * The labels to show. Called when the view or the lit web changes, not on a still frame. `x, y` are canvas pixels, the
   * middle of the label's bottom edge; scene-labels.ts lists the names `cls` holds.
   */
  onLabels(placed: PlacedLabel[]): void;
  /** The WebGL context went and did not come back: this scene will draw nothing more. */
  onLost(): void;
}

/** Where `focus` flies: a fixed view of the whole machine, the node's own shot, or nowhere. */
export type FocusCam = "overview" | "node" | "yours" | "clock" | "keep";

export interface SceneApi {
  /** Light the web of node `i` in `mode` and dim the rest; null lights nothing. */
  setWeb(i: number | null, mode: WebMode): void;
  /** Fly (or, under reduced motion, cut) to a view. "node" frames `i`, or the whole machine when `i` is null; "keep" stays put. */
  focus(i: number | null, cam: FocusCam): void;
  /** The pixels open panels take at the left, right and bottom, so the machine centres in what is left. */
  setViewOffset(leftPx: number, rightPx: number, bottomPx: number): void;
  /** The runs as they stand: the running ones pulse at their job, with a progress arc when they report one. */
  setRuns(runs: Run[]): void;
  /** The replay: `frac` of the way across the last day, `active` the job nodes to light; null ends it and restores the selection. */
  setReplay(frac: number | null, active: number[]): void;
  zoom(deltaY: number): void;
  dispose(): void;
}

const FOV = 30;
const MAX_PIXEL_RATIO = 2;
/** A press that moves further than this is a drag, not a click. */
const CLICK_SLOP = 5;
/** A fingertip rolls as it presses, so a tap may move this far. */
const TOUCH_SLOP = 10;
/** Pulses along a fibre, and running jobs' rings, in cycles a second. */
const PULSE_RATE = 0.42;
const RUN_RATE = 0.5;
/** Seconds between readings of the hand: at a quarter of a degree a minute it needs no more. */
const HAND_EVERY_S = 10;
/** How much of the way to its target the view offset eases each frame. */
const OFFSET_EASE = 0.08;
/** The furthest the camera goes, before `frameFit` stands it further back on a narrow canvas. */
const MAX_DISTANCE = 60;
/** How long a lost WebGL context has to come back, with the page in sight, before the scene gives up on it. */
const LOST_GRACE_MS = 3000;

export function createScene(
  canvas: HTMLCanvasElement,
  gi: GraphIndex,
  opts: { palette: Palette; reducedMotion: boolean; nowNY: () => number },
  h: SceneHandlers,
): SceneApi {
  const { palette, reducedMotion, nowNY } = opts;
  const { nodes } = gi.out;
  const { pos, angle } = layout(gi.out, gi.byId);
  const isNode = (i: number | null): i is number => i !== null && Number.isInteger(i) && i >= 0 && i < nodes.length;

  const renderer = new WebGLRenderer({ canvas, antialias: true });
  const pixelRatio = Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
  renderer.setPixelRatio(pixelRatio);
  renderer.setClearColor(palette.ground, 1);
  renderer.outputColorSpace = LinearSRGBColorSpace; // display space end to end: palette.ts says why
  // Draw in the order added: the structure, then the additive marks over it. three would otherwise sort by each
  // geometry's distance, which changes as the camera turns, and a ring drawn over a node paints a dark slash across it.
  renderer.sortObjects = false;

  const scene = new Scene();
  const camera = new PerspectiveCamera(FOV, 1, 0.1, 400);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = !reducedMotion;
  controls.dampingFactor = 0.08;
  controls.rotateSpeed = 0.55;
  controls.minDistance = 4;
  controls.maxDistance = MAX_DISTANCE;
  controls.minPolarAngle = 0.12;
  controls.maxPolarAngle = 1.38;
  controls.enablePan = false;
  controls.enableZoom = false; // the wheel is the page's: it steps the tour, or calls zoom()
  camera.position.set(0, 14, 24);
  controls.target.set(...OVERVIEW.target);
  controls.update();

  const structure = createStructure(gi, palette);
  const edges = createEdges(gi, pos, angle, palette, reducedMotion);
  const marks = createNodes(gi, pos, palette, pixelRatio);
  const rings = createRunRings(pos, palette);
  scene.add(structure.object, edges.object, marks.object, rings.object);
  const flight = createFlight(camera, controls, reducedMotion);
  const labels = labelBook(gi, pos, angle);

  // ------------------------------------------------------------------ what is lit
  let sel: number | null = null;
  let mode: WebMode = "web";
  let replay: number[] | null = null;
  let replayFrom = 0;
  let running = new Set<number>();
  let handHour = nowNY();
  let specs: LabelSpec[] = [];
  let labelsStale = true;

  const litWebs = (): CentreWeb[] => (replay ?? (sel === null ? [] : [sel])).map((centre) => ({ centre, web: webOf(gi, centre, mode) }));

  const paint = (): void => {
    const webs = litWebs();
    marks.paint(webs, replay !== null);
    edges.paint(webs, running);
    specs = labels.active(webs, handHour);
    labelsStale = true;
  };

  const readHand = (): void => {
    handHour = nowNY();
    structure.setHand(handHour);
    specs = labels.active(litWebs(), handHour);
    labelsStale = true;
  };

  // ------------------------------------------------------------------ the view
  let width = 1;
  let height = 1;
  let fit = 1;
  const offset = { x: 0, y: 0, wantX: 0, wantY: 0, placed: false };
  const applyOffset = (): void => camera.setViewOffset(width, height, offset.x, offset.y, width, height);

  const resize = (): void => {
    const w = canvas.clientWidth, tall = canvas.clientHeight;
    if (!w || !tall || (w === width && tall === height)) return;
    width = w;
    height = tall;
    renderer.setSize(w, tall, false);
    camera.aspect = w / tall;
    applyOffset();
    const nextFit = frameFit(w / tall);
    camera.position.sub(controls.target).multiplyScalar(nextFit / fit).add(controls.target);
    fit = nextFit;
    controls.maxDistance = MAX_DISTANCE * fit;
    // a size that keeps the aspect leaves the matrix as it was, and every screen position is still out of date
    projectionStale = true;
    labelsStale = true;
  };
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
  observer?.observe(canvas);

  const viewProjection = new Matrix4();
  const lastViewProjection = new Matrix4();
  const screen = new Float32Array(nodes.length * 2);
  const isSeries = Uint8Array.from(nodes, (n) => (n.k === "series" ? 1 : 0));
  let projectionStale = true;

  // ------------------------------------------------------------------ the pointer
  let pointer: { x: number; y: number } | null = null;
  let press: { x: number; y: number } | null = null;
  let hover: number | null = null;
  let pointerStale = false;
  /** The fingers on the canvas. */
  const touches = new Set<number>();

  const pickAt = (clientX: number, clientY: number, reach = 1): number | null => {
    if (projectionStale) {
      projectNodes(viewProjection.elements, pos, width, height, screen);
      projectionStale = false;
    }
    const box = canvas.getBoundingClientRect();
    return nearestNode(screen, isSeries, clientX - box.left, clientY - box.top, reach);
  };

  const refreshHover = (): void => {
    pointerStale = false;
    const i = pointer && !press ? pickAt(pointer.x, pointer.y) : null;
    canvas.style.cursor = press ? "grabbing" : i === null ? "grab" : "pointer";
    if (i === null && hover === null) return;
    hover = i;
    h.onHover(i, pointer?.x ?? 0, pointer?.y ?? 0);
  };

  const onPointerMove = (e: PointerEvent): void => {
    pointer = { x: e.clientX, y: e.clientY };
    pointerStale = true;
  };
  const onPointerLeave = (): void => {
    pointer = null;
    pointerStale = true;
  };
  const onPointerDown = (e: PointerEvent): void => {
    press = e.isPrimary && e.button === 0 ? { x: e.clientX, y: e.clientY } : null;
    pointerStale = true;
    if (e.pointerType === "touch") touches.add(e.pointerId);
    // a pinch is the visitor's own zoom, and only fingers can make one; the wheel stays the page's, also on a laptop with a touch screen
    controls.enableZoom = touches.size > 0;
  };
  const lift = (e: PointerEvent): void => {
    touches.delete(e.pointerId);
    controls.enableZoom = touches.size > 0;
  };
  const onPointerUp = (e: PointerEvent): void => {
    const start = press;
    const touch = e.pointerType === "touch";
    press = null;
    pointer = touch ? null : { x: e.clientX, y: e.clientY };
    pointerStale = true;
    lift(e);
    if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) <= (touch ? TOUCH_SLOP : CLICK_SLOP)) h.onPick(pickAt(e.clientX, e.clientY, touch ? TOUCH_REACH : 1));
  };
  const onPointerCancel = (e: PointerEvent): void => {
    press = null;
    pointerStale = true;
    lift(e);
  };
  const onControlStart = (): void => flight.cancel();

  // capture, so it runs before OrbitControls reads enableZoom for the same event
  canvas.addEventListener("pointerdown", onPointerDown, { capture: true });
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerleave", onPointerLeave);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("pointercancel", onPointerCancel);
  controls.addEventListener("start", onControlStart);
  canvas.style.cursor = "grab";

  // ------------------------------------------------------------------ a lost context
  // three holds the context ready to come back and redraws when it does. Browsers restore it only for a page in sight
  // (a phone takes it from a page in the background), so the grace runs only then.
  let lost = false;
  let grace = 0;
  const waitForContext = (): void => {
    clearTimeout(grace);
    if (lost && !document.hidden) grace = window.setTimeout(() => h.onLost(), LOST_GRACE_MS);
  };
  const onContextLost = (): void => {
    lost = true;
    waitForContext();
  };
  const onContextRestored = (): void => {
    lost = false;
    waitForContext();
  };
  canvas.addEventListener("webglcontextlost", onContextLost);
  canvas.addEventListener("webglcontextrestored", onContextRestored);
  document.addEventListener("visibilitychange", waitForContext);

  // ------------------------------------------------------------------ the frame
  let frame = 0;
  let lastHand = performance.now() / 1000;

  const tick = (): void => {
    frame = requestAnimationFrame(tick);
    const nowS = performance.now() / 1000;
    flight.step(nowS);
    controls.update();

    if (Math.abs(offset.wantX - offset.x) > 0.01 || Math.abs(offset.wantY - offset.y) > 0.01) {
      offset.x += (offset.wantX - offset.x) * OFFSET_EASE;
      offset.y += (offset.wantY - offset.y) * OFFSET_EASE;
      applyOffset();
    }

    if (!reducedMotion) {
      edges.setPulse((nowS * PULSE_RATE) % 1);
      rings.setPhase((nowS * RUN_RATE) % 1);
    }
    if (nowS - lastHand >= HAND_EVERY_S) {
      lastHand = nowS;
      readHand();
    }

    camera.updateMatrixWorld();
    viewProjection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    if (!sameView(viewProjection.elements, lastViewProjection.elements)) {
      lastViewProjection.copy(viewProjection);
      projectionStale = true;
      labelsStale = true;
      pointerStale = pointerStale || pointer !== null;
    }
    if (pointerStale) refreshHover();
    if (labelsStale) {
      labelsStale = false;
      h.onLabels(placeFrame(specs, viewProjection.elements, width, height));
    }
    renderer.render(scene, camera);
  };

  resize();
  structure.setHand(handHour);
  paint();
  frame = requestAnimationFrame(tick);

  return {
    setWeb(i, m) {
      sel = isNode(i) ? i : null;
      mode = m;
      paint();
    },
    focus(i, cam) {
      if (cam === "keep") return;
      let shot: Shot = cam === "yours" ? YOURS : cam === "clock" ? CLOCK : OVERVIEW;
      if (cam === "node" && isNode(i)) {
        const web = webOf(gi, i, mode);
        const p: Vec3 = [pos[3 * i], pos[3 * i + 1], pos[3 * i + 2]];
        shot = nodeShot(nodes[i].k, radiusOf(nodes[i].ring), angle[i], p, web.up.size + web.down.size);
      }
      flight.to({ ...shot, radius: shot.radius * fit }, performance.now() / 1000);
    },
    setViewOffset(leftPx, rightPx, bottomPx) {
      offset.wantX = (rightPx - leftPx) / 2;
      offset.wantY = bottomPx / 2;
      if (reducedMotion || !offset.placed) {
        offset.placed = true;
        offset.x = offset.wantX;
        offset.y = offset.wantY;
        applyOffset();
      }
    },
    setRuns(runs) {
      const byNode = new Map<number, RunMark>();
      for (const r of runningRuns(runs)) {
        const node = gi.byId.get(`j:${r.kind}`);
        if (node === undefined || byNode.has(node)) continue;
        const p = r.progress;
        byNode.set(node, { node, progress: typeof p === "number" && Number.isFinite(p) ? Math.min(1, Math.max(0, p)) : null });
      }
      rings.set([...byNode.values()]);
      const next = new Set(byNode.keys());
      if (next.size === running.size && [...next].every((n) => running.has(n))) return;
      running = next;
      paint();
    },
    setReplay(frac, active) {
      if (frac === null) {
        structure.setReplayHand(null);
        if (replay === null) return;
        replay = null;
        paint();
        return;
      }
      if (replay === null) replayFrom = nowNY();
      const f = Number.isFinite(frac) ? Math.min(1, Math.max(0, frac)) : 0;
      structure.setReplayHand((replayFrom + 24 * f) % 24);
      const next = active.filter(isNode);
      if (replay !== null && next.length === replay.length && next.every((n, k) => n === replay![k])) return;
      replay = next;
      paint();
    },
    zoom(deltaY) {
      flight.cancel();
      const toCamera = new Vector3().subVectors(camera.position, controls.target);
      const distance = zoomDistance(toCamera.length(), deltaY, controls.minDistance, controls.maxDistance);
      camera.position.copy(controls.target).addScaledVector(toCamera.normalize(), distance);
    },
    dispose() {
      cancelAnimationFrame(frame);
      clearTimeout(grace);
      observer?.disconnect();
      canvas.removeEventListener("webglcontextlost", onContextLost);
      canvas.removeEventListener("webglcontextrestored", onContextRestored);
      document.removeEventListener("visibilitychange", waitForContext);
      canvas.removeEventListener("pointerdown", onPointerDown, { capture: true });
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerleave", onPointerLeave);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("pointercancel", onPointerCancel);
      controls.removeEventListener("start", onControlStart);
      controls.dispose();
      structure.dispose();
      edges.dispose();
      marks.dispose();
      rings.dispose();
      renderer.dispose(); // no forceContextLoss: React may build a new scene on this same canvas, and a lost context stays lost
    },
  };
}
