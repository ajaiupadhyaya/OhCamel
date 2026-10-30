/**
 * The Machine's view as an address: `?sel=<node id>&tab=tour|read|day&step=<1-8>`, so that every view is a deep
 * link. Pure, no DOM and no router.
 *
 * The address is untrusted input (anyone can write one): `parseView` keeps only what the page can show. A
 * field that is not valid falls back to its default on its own, so a bad `sel` does not cost the tab or the
 * step. Nothing read here is ever written as HTML.
 */
import type { TourCam } from "./tour";

export const TABS = ["tour", "read", "day"] as const;
export type Tab = (typeof TABS)[number];

/** The number of steps in the tour. */
const STEPS = 8;

export interface View {
  /** The selected node's id, or null for none. */
  sel: string | null;
  tab: Tab;
  /** The tour's step, 1 to 8. */
  step: number;
}

const DEFAULT_TAB: Tab = "tour";
const DEFAULT_STEP = 1;

const isTab = (v: unknown): v is Tab => TABS.some((t) => t === v);
const isStep = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v) && v >= 1 && v <= STEPS;

/**
 * The view an address names. `has` says whether a node id exists in the graph: a `sel` it does not know is
 * dropped. A `tab` that is not one of the three is "tour"; a `step` that is not a plain whole number from 1 to 8
 * (leading zeros allowed) is 1. A field given twice reads as the first. It never throws, whatever the address
 * holds, malformed percent-encoding included.
 */
export function parseView(search: string, has: (id: string) => boolean): View {
  const view: View = { sel: null, tab: DEFAULT_TAB, step: DEFAULT_STEP };
  if (typeof search !== "string") return view;
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(search);
  } catch {
    return view;
  }
  const sel = params.get("sel");
  if (sel !== null && sel !== "" && has(sel)) view.sel = sel;
  const tab = params.get("tab");
  if (isTab(tab)) view.tab = tab;
  const step = params.get("step");
  if (step !== null && /^0*[1-8]$/.test(step)) view.step = Number(step);
  return view;
}

/** A query value, percent-encoded but for the colon and the at sign, which node ids carry and which a query allows. */
const encode = (s: string): string => encodeURIComponent(s).replace(/%3A/g, ":").replace(/%40/g, "@");

/**
 * The address of a view: `?sel=…&tab=…&step=…`, with every field that is its default left out, so the default
 * view is the empty string. A tab or step that `parseView` would not accept is left out too.
 */
export function viewSearch(v: View): string {
  const parts: string[] = [];
  if (typeof v.sel === "string" && v.sel !== "") parts.push(`sel=${encode(v.sel)}`);
  if (isTab(v.tab) && v.tab !== DEFAULT_TAB) parts.push(`tab=${v.tab}`);
  if (isStep(v.step) && v.step !== DEFAULT_STEP) parts.push(`step=${v.step}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** A tour step as a view sees it: the node it selects, by id, and where it puts the camera. */
export interface StepView {
  sel: string | null;
  cam: TourCam;
}

/** Where a view's camera belongs, with `step` its tour step: what a new scene is flown back to. It never changes the view. */
export function viewCam(v: View, step: StepView): TourCam {
  if (v.tab === "tour") return step.cam;
  return v.sel === null ? "overview" : "node";
}

/**
 * What arriving at an address does, on loading the page or when the address changes from outside it (the wordmark,
 * `FRONT`, back and forward): the view to go to, and the camera's way there. A tour step brings its own selection. A
 * selection its step does not name opens its reading, so `/?sel=d:AAPL` is not step 1 with an unrelated panel.
 */
export function arrival(v: View, step: StepView): { view: View; cam: TourCam } {
  if (v.tab !== "tour") return { view: v, cam: viewCam(v, step) };
  if (v.sel === null || v.sel === step.sel) return { view: { ...v, sel: step.sel }, cam: step.cam };
  return { view: { ...v, tab: "read" }, cam: "node" };
}
