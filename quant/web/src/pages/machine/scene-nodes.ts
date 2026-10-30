/**
 * The machine's nodes: one additive `Points`, a soft disc with a crisp core, drawn entirely in the fragment shader.
 * Colours and sizes are baked once; a selection rewrites one attribute, the glow.
 */
import { AdditiveBlending, BufferAttribute, BufferGeometry, Points, ShaderMaterial } from "three";
import type { Palette } from "./palette";
import { glowOf, nodeSize, nodeTone } from "./scene-geom";
import type { CentreWeb } from "./scene-geom";
import type { GraphIndex } from "./web";

/** The selected node, and a job the replay's hand is inside. */
const SELECTED_GLOW = 1.8;
const REPLAY_GLOW = 2.2;

const VERTEX = /* glsl */ `
  uniform float uPixelRatio;
  attribute vec3 aColor;
  attribute float aSize, aGlow;
  varying vec3 vColor;
  varying float vGlow;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = aSize * uPixelRatio * clamp(24.0 / -mv.z, 0.55, 2.4) * (0.8 + 0.35 * clamp(aGlow, 0.0, 1.5));
    vColor = aColor;
    vGlow = aGlow;
  }`;

const FRAGMENT = /* glsl */ `
  varying vec3 vColor;
  varying float vGlow;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    if (d > 0.5) discard;
    float core = 1.0 - smoothstep(0.2, 0.34, d);
    float halo = (1.0 - smoothstep(0.3, 0.5, d)) * 0.28;
    float level = vGlow < 0.05 ? 0.13 : mix(0.13, 1.0, clamp(vGlow, 0.0, 1.0)) * (1.0 + 0.5 * max(vGlow - 1.0, 0.0));
    gl_FragColor = vec4(vColor * (core + halo) * level, 1.0);
  }`;

export interface NodeMarks {
  object: Points;
  /** Light these webs; `replay` when the replay lit them. */
  paint(webs: readonly CentreWeb[], replay: boolean): void;
  dispose(): void;
}

export function createNodes(gi: GraphIndex, pos: Float32Array, palette: Palette, pixelRatio: number): NodeMarks {
  const { nodes } = gi.out;
  const colors = new Float32Array(nodes.length * 3);
  const sizes = new Float32Array(nodes.length);
  const glows = new Float32Array(nodes.length).fill(glowOf(0, [], 0));
  nodes.forEach((n, i) => {
    const { token, alpha } = nodeTone(n);
    const c = palette[token];
    colors[3 * i] = c.r * alpha;
    colors[3 * i + 1] = c.g * alpha;
    colors[3 * i + 2] = c.b * alpha;
    sizes[i] = nodeSize(n);
  });

  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(pos, 3));
  geometry.setAttribute("aColor", new BufferAttribute(colors, 3));
  geometry.setAttribute("aSize", new BufferAttribute(sizes, 1));
  const glowAttribute = new BufferAttribute(glows, 1);
  geometry.setAttribute("aGlow", glowAttribute);

  const material = new ShaderMaterial({
    uniforms: { uPixelRatio: { value: pixelRatio } },
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
    transparent: true,
    depthTest: false,
    depthWrite: false,
    blending: AdditiveBlending,
  });

  const object = new Points(geometry, material);
  object.frustumCulled = false;

  return {
    object,
    paint(webs, replay) {
      for (let i = 0; i < glows.length; i++) glows[i] = glowOf(i, webs, replay ? REPLAY_GLOW : SELECTED_GLOW);
      glowAttribute.needsUpdate = true;
    },
    dispose() {
      geometry.dispose();
      material.dispose();
    },
  };
}
