/**
 * The jobs running now: at each one, a ring that pulses outward from the node and an arc of its progress, flat on the
 * clock's plane and running clockwise from the far side like the clock itself. A run that reports no progress gets
 * no arc: there is nothing to draw it from.
 */
import { AdditiveBlending, BufferGeometry, Float32BufferAttribute, LineSegments, ShaderMaterial } from "three";
import type { Palette } from "./palette";

/** Segments in a full circle. */
const CIRCLE = 48;
const TAU = Math.PI * 2;

/** One running job: its node, and how far it has come, 0 to 1, if it says. */
export interface RunMark {
  node: number;
  progress: number | null;
}

const VERTEX = /* glsl */ `
  uniform float uPhase;
  attribute vec3 aRim;
  attribute float aPulse;
  varying float vLevel;
  void main() {
    float reach = aPulse > 0.5 ? 0.14 + 0.22 * uPhase : 0.27;
    vLevel = aPulse > 0.5 ? 0.75 * (1.0 - uPhase) : 0.85;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position + aRim * reach, 1.0);
  }`;

const FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  varying float vLevel;
  void main() { gl_FragColor = vec4(uColor * vLevel, 1.0); }`;

export interface RunRings {
  object: LineSegments;
  /** Draw these jobs and no others. */
  set(marks: readonly RunMark[]): void;
  /** How far out the pulse has gone, 0 to 1. Left alone, it holds still at its middle. */
  setPhase(phase: number): void;
  dispose(): void;
}

export function createRunRings(pos: Float32Array, palette: Palette): RunRings {
  const material = new ShaderMaterial({
    uniforms: { uPhase: { value: 0.5 }, uColor: { value: palette.bright } },
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    transparent: true,
    depthTest: false,
    depthWrite: false,
    blending: AdditiveBlending,
  });
  const object = new LineSegments(new BufferGeometry(), material);
  object.frustumCulled = false;
  object.visible = false;

  return {
    object,
    set(marks) {
      const position: number[] = [], rim: number[] = [], pulse: number[] = [];
      const vertex = (node: number, s: number, isPulse: number): void => {
        const a = (s / CIRCLE) * TAU;
        position.push(pos[3 * node], pos[3 * node + 1], pos[3 * node + 2]);
        rim.push(Math.sin(a), 0, -Math.cos(a));
        pulse.push(isPulse);
      };
      for (const { node, progress } of marks) {
        for (let s = 0; s < CIRCLE; s++) {
          vertex(node, s, 1);
          vertex(node, s + 1, 1);
        }
        const arc = progress === null ? 0 : Math.round(progress * CIRCLE);
        for (let s = 0; s < arc; s++) {
          vertex(node, s, 0);
          vertex(node, s + 1, 0);
        }
      }
      // a fresh geometry, not new attributes on the old: three frees a buffer only with the geometry that holds it
      object.geometry.dispose();
      const geometry = new BufferGeometry();
      geometry.setAttribute("position", new Float32BufferAttribute(position, 3));
      geometry.setAttribute("aRim", new Float32BufferAttribute(rim, 3));
      geometry.setAttribute("aPulse", new Float32BufferAttribute(pulse, 1));
      object.geometry = geometry;
      object.visible = marks.length > 0;
    },
    setPhase(phase) {
      material.uniforms.uPhase.value = phase;
    },
    dispose() {
      object.geometry.dispose();
      material.dispose();
    },
  };
}
