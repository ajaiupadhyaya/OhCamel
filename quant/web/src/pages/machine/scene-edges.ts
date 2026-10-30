/**
 * The machine's edges: every real dependency as a fibre of 16 segments arching toward its target, all in one indexed
 * `LineSegments`. Each fibre has its own run of vertices, so `level` and `flow` hold one value per fibre, and they are
 * rewritten only when the web changes. A frame's only work here is one uniform, the pulse's phase.
 */
import { AdditiveBlending, BufferAttribute, BufferGeometry, LineSegments, ShaderMaterial } from "three";
import type { Palette } from "./palette";
import { edgeStyle, fibreControl, radiusOf, SEGMENTS, writeFibre } from "./scene-geom";
import type { CentreWeb, Vec3 } from "./scene-geom";
import type { GraphIndex } from "./web";

const POINTS = SEGMENTS + 1;
/** Spreads the pulses' phases evenly along the edges (the golden ratio), so two fibres of a web rarely pulse together, and a reload draws the same picture. */
const PHASE_STEP = 0.618033988749895;
/**
 * The loom's colour is `structure` at this many times its brightness, which is the approved prototype's fibre colour
 * (0.42, 0.55, 0.62), from the token. A level of 0.07 is multiplied by this colour, so a darker one loses the loom: at
 * `line` a fibre comes to 3 parts in 255 and all but the densest crossings go black.
 */
const LOOM_GAIN = 2.4;

const VERTEX = /* glsl */ `
  attribute float aT, aPhase, level, flow;
  varying float vT, vPhase, vLevel, vFlow;
  void main() {
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    vT = aT;
    vPhase = aPhase;
    vLevel = level;
    vFlow = flow;
  }`;

// aT runs 0 at the source to 1 at the target, so subtracting a rising uPulse moves the crest toward the target: the way data flows.
const FRAGMENT = /* glsl */ `
  uniform vec3 uLoom, uReads, uFeeds;
  uniform float uPulse, uMotion;
  varying float vT, vPhase, vLevel, vFlow;
  void main() {
    float p = fract(vT - uPulse + vPhase);
    float pulse = uMotion * smoothstep(0.0, 0.11, p) * (1.0 - smoothstep(0.11, 0.14, p));
    bool down = vFlow > 1.5 && vFlow < 2.5;
    bool web = vFlow > 0.5 && vFlow < 2.5;
    bool run = vFlow > 2.5;
    vec3 color = down ? uFeeds : web || run ? uReads : uLoom;
    float level = web ? 0.10 + 0.42 * vLevel + pulse * 1.3 * vLevel : run ? vLevel + pulse * 0.7 : vLevel;
    gl_FragColor = vec4(color * level, 1.0);
  }`;

export interface EdgeMarks {
  object: LineSegments;
  /** Light these webs, with pulses on the edges into each job in `running`. */
  paint(webs: readonly CentreWeb[], running: ReadonlySet<number>): void;
  /** Where the pulses are, 0 to 1 along a fibre. */
  setPulse(phase: number): void;
  dispose(): void;
}

export function createEdges(gi: GraphIndex, pos: Float32Array, angle: Float32Array, palette: Palette, reducedMotion: boolean): EdgeMarks {
  const { nodes, edges } = gi.out;
  const ends = [...gi.edgeAt.values()].map((ei) => edges[ei]);
  const count = ends.length;
  const position = new Float32Array(count * POINTS * 3);
  const t = new Float32Array(count * POINTS);
  const phase = new Float32Array(count * POINTS);
  const level = new Float32Array(count * POINTS);
  const flow = new Float32Array(count * POINTS);
  const index = new Uint32Array(count * SEGMENTS * 2);

  const point = (i: number): Vec3 => [pos[3 * i], pos[3 * i + 1], pos[3 * i + 2]];
  ends.forEach(([a, b], k) => {
    const first = k * POINTS;
    const control = fibreControl(radiusOf(nodes[a].ring), angle[a], radiusOf(nodes[b].ring), angle[b]);
    writeFibre(position, first, point(a), control, point(b));
    phase.fill((k * PHASE_STEP) % 1, first, first + POINTS);
    for (let s = 0; s < POINTS; s++) t[first + s] = s / SEGMENTS;
    for (let s = 0; s < SEGMENTS; s++) {
      index[(k * SEGMENTS + s) * 2] = first + s;
      index[(k * SEGMENTS + s) * 2 + 1] = first + s + 1;
    }
  });

  const geometry = new BufferGeometry();
  geometry.setIndex(new BufferAttribute(index, 1));
  geometry.setAttribute("position", new BufferAttribute(position, 3));
  geometry.setAttribute("aT", new BufferAttribute(t, 1));
  geometry.setAttribute("aPhase", new BufferAttribute(phase, 1));
  const levelAttribute = new BufferAttribute(level, 1);
  const flowAttribute = new BufferAttribute(flow, 1);
  geometry.setAttribute("level", levelAttribute);
  geometry.setAttribute("flow", flowAttribute);

  const material = new ShaderMaterial({
    uniforms: {
      uLoom: { value: palette.structure.clone().multiplyScalar(LOOM_GAIN) },
      uReads: { value: palette.reads },
      uFeeds: { value: palette.feeds },
      uPulse: { value: 0 },
      uMotion: { value: reducedMotion ? 0 : 1 },
    },
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    transparent: true,
    depthTest: false,
    depthWrite: false,
    blending: AdditiveBlending,
  });

  const object = new LineSegments(geometry, material);
  object.frustumCulled = false;

  const paint = (webs: readonly CentreWeb[], running: ReadonlySet<number>): void => {
    ends.forEach(([a, b], k) => {
      const style = edgeStyle(a, b, webs, running);
      level.fill(style.level, k * POINTS, (k + 1) * POINTS);
      flow.fill(style.flow, k * POINTS, (k + 1) * POINTS);
    });
    levelAttribute.needsUpdate = true;
    flowAttribute.needsUpdate = true;
  };
  paint([], new Set());

  return {
    object,
    paint,
    setPulse(p) {
      material.uniforms.uPulse.value = p;
    },
    dispose() {
      geometry.dispose();
      material.dispose();
    },
  };
}
