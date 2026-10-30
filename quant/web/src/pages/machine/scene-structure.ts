/**
 * The machine's structure: the ring circles, the clock's hour ticks, a tick for every scheduled fire, the market
 * session's arc, and the hands. All of it is thin lines at fixed places; only the hands turn.
 */
import { BufferGeometry, Float32BufferAttribute, Group, Line, LineBasicMaterial, LineSegments } from "three";
import type { Color } from "three";
import { HEIGHT, hourAngle, RADIUS } from "./layout";
import type { Palette } from "./palette";
import { fireTickVerts, hourTickVerts, ringVerts, sessionVerts } from "./scene-geom";
import type { GraphIndex } from "./web";

const NOW_LENGTH = RADIUS.clock + 0.5;
const REPLAY_LENGTH = RADIUS.clock + 0.55;

export interface Structure {
  object: Group;
  /** The hand at New York hour `h`. */
  setHand(h: number): void;
  /** The replay's hand at New York hour `h`, or none. */
  setReplayHand(h: number | null): void;
  dispose(): void;
}

export function createStructure(gi: GraphIndex, palette: Palette): Structure {
  const object = new Group();
  const disposers: (() => void)[] = [];

  const material = (color: Color, opacity: number): LineBasicMaterial => {
    const m = new LineBasicMaterial({ color, transparent: true, opacity, depthTest: false, depthWrite: false });
    disposers.push(() => m.dispose());
    return m;
  };
  const geometryOf = (verts: number[]): BufferGeometry => {
    const g = new BufferGeometry();
    g.setAttribute("position", new Float32BufferAttribute(verts, 3));
    disposers.push(() => g.dispose());
    return g;
  };
  const segments = (verts: number[], color: Color, opacity: number): void => {
    const lines = new LineSegments(geometryOf(verts), material(color, opacity));
    lines.frustumCulled = false;
    object.add(lines);
  };

  segments([...ringVerts(), ...hourTickVerts()], palette.structure, 0.9);
  segments(fireTickVerts(gi.out.x?.fires ?? []), palette.dim, 1);
  segments(sessionVerts(), palette.reads, 0.45);

  /** A hand: a line from the centre along angle 0, turned about the vertical to its hour. */
  const hand = (length: number, color: Color, opacity: number): Line => {
    const line = new Line(geometryOf([0, 0, 0, 0, 0, -length]), material(color, opacity));
    line.position.y = HEIGHT * RADIUS.clock;
    line.frustumCulled = false;
    object.add(line);
    return line;
  };
  const now = hand(NOW_LENGTH, palette.bright, 0.35);
  const replay = hand(REPLAY_LENGTH, palette.bright, 1);
  replay.visible = false;

  return {
    object,
    setHand(h) {
      now.rotation.y = -hourAngle(h);
    },
    setReplayHand(h) {
      replay.visible = h !== null;
      if (h !== null) replay.rotation.y = -hourAngle(h);
    },
    dispose() {
      disposers.forEach((d) => d());
    },
  };
}
