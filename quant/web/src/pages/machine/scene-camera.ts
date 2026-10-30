/**
 * The camera's flights: 1.5 s with ease-in-out-cubic, the short way round, or a cut under reduced motion. Anything the
 * visitor does to the camera ends a flight, so a drag is never fought.
 */
import { Spherical, Vector3 } from "three";
import type { PerspectiveCamera } from "three";
import type { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { ease, shortTheta, shotAt } from "./scene-geom";
import type { Shot } from "./scene-geom";

const FLIGHT_S = 1.5;

export interface Flight {
  /** Go to `shot`: a flight, or at once under reduced motion. */
  to(shot: Shot, nowS: number): void;
  /** Move the camera along for a frame at `nowS`. */
  step(nowS: number): void;
  /** End any flight where it is. */
  cancel(): void;
}

export function createFlight(camera: PerspectiveCamera, controls: OrbitControls, reducedMotion: boolean): Flight {
  const offset = new Vector3();
  const spherical = new Spherical();
  let path: { from: Shot; to: Shot; start: number } | null = null;

  const place = (s: Shot): void => {
    controls.target.set(...s.target);
    spherical.set(s.radius, s.phi, s.theta);
    camera.position.setFromSpherical(spherical).add(controls.target);
  };

  return {
    to(shot, nowS) {
      if (reducedMotion) {
        path = null;
        place(shot);
        return;
      }
      spherical.setFromVector3(offset.copy(camera.position).sub(controls.target));
      const from: Shot = { target: [controls.target.x, controls.target.y, controls.target.z], radius: spherical.radius, phi: spherical.phi, theta: spherical.theta };
      path = { from, to: { ...shot, theta: shortTheta(from.theta, shot.theta) }, start: nowS };
    },
    step(nowS) {
      if (!path) return;
      const f = Math.min(1, (nowS - path.start) / FLIGHT_S);
      place(shotAt(path.from, path.to, ease(f)));
      if (f >= 1) path = null;
    },
    cancel() {
      path = null;
    },
  };
}
