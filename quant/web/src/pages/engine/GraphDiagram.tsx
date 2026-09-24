/**
 * An explanatory (not live) picture of the engine's Incremental dependency graph: pick a
 * price feed to "tick" and the nodes downstream of it light up in the order they are
 * recomputed; everything else is reused from the previous stabilization. The node set
 * mirrors the engine's structure (prices → returns windows → covariance → VaR/ES; prices ×
 * quantities → exposures → gross/net; both → limits) for three symbols.
 */
import { useEffect, useMemo, useState } from "react";

interface Node {
  id: string;
  label: string;
  sub?: string;
  col: number;
  row: number;
  kind: "input" | "compute" | "output";
}

const SYMS = ["SPY", "TLT", "GLD"];
const NODES: Node[] = [
  ...SYMS.map((s, i) => ({ id: `tick:${s}`, label: `${s} tick`, sub: "feed", col: 0, row: i * 2, kind: "input" as const })),
  { id: "qty", label: "Quantities", sub: "positions", col: 0, row: 6, kind: "input" },
  ...SYMS.map((s, i) => ({ id: `px:${s}`, label: `${s} price`, sub: "mark", col: 1, row: i * 2, kind: "compute" as const })),
  ...SYMS.map((s, i) => ({ id: `ret:${s}`, label: `${s} returns`, sub: "window", col: 2, row: i * 2, kind: "compute" as const })),
  ...SYMS.map((s, i) => ({ id: `exp:${s}`, label: `${s} exposure`, sub: "P × q", col: 2, row: i * 2 + 1, kind: "compute" as const })),
  { id: "cov", label: "Covariance Σ", sub: "EWMA / sample", col: 3, row: 1, kind: "compute" },
  { id: "book", label: "Gross / net", sub: "exposure", col: 3, row: 4, kind: "compute" },
  { id: "var", label: "VaR / ES", sub: "hist · param · EWMA", col: 4, row: 1, kind: "compute" },
  { id: "euler", label: "Component VaR", sub: "Euler", col: 4, row: 3, kind: "compute" },
  { id: "limits", label: "Limits", sub: "utilisation", col: 5, row: 2.5, kind: "output" },
  { id: "alpha", label: "Confidence α", sub: "config", col: 3, row: 6, kind: "input" },
];

const EDGES: [string, string][] = [
  ...SYMS.flatMap((s): [string, string][] => [
    [`tick:${s}`, `px:${s}`],
    [`px:${s}`, `ret:${s}`],
    [`px:${s}`, `exp:${s}`],
    ["qty", `exp:${s}`],
    [`ret:${s}`, "cov"],
    [`exp:${s}`, "book"],
    [`exp:${s}`, "euler"],
  ]),
  ["cov", "var"],
  ["cov", "euler"],
  ["book", "var"],
  ["alpha", "var"],
  ["var", "euler"],
  ["var", "limits"],
  ["euler", "limits"],
  ["book", "limits"],
];

const W = 980;
const H = 330;
const COLX = (c: number) => 20 + c * 160;
const ROWY = (r: number) => 22 + r * 44;
const NW = 132;
const NH = 34;

/** Nodes reachable from `src`, with their depth (recompute order). */
function downstream(src: string): Map<string, number> {
  const out = new Map<string, number>([[src, 0]]);
  const q = [src];
  while (q.length) {
    const n = q.shift()!;
    for (const [a, b] of EDGES)
      if (a === n) {
        const d = out.get(n)! + 1;
        if (!out.has(b) || out.get(b)! < d) {
          out.set(b, d);
          q.push(b);
        }
      }
  }
  return out;
}

export function GraphDiagram() {
  const [src, setSrc] = useState("tick:SPY");
  const [step, setStep] = useState(99);
  const dirty = useMemo(() => downstream(src), [src]);
  const maxDepth = Math.max(...dirty.values());
  const pos = useMemo(() => Object.fromEntries(NODES.map((n) => [n.id, { x: COLX(n.col), y: ROWY(n.row) }])), []);

  // Replay the propagation one level at a time after each tick.
  useEffect(() => {
    if (step >= maxDepth) return;
    const t = window.setTimeout(() => setStep((s) => s + 1), 380);
    return () => window.clearTimeout(t);
  }, [step, maxDepth]);

  const tick = (id: string) => {
    setSrc(id);
    setStep(0);
  };
  const lit = (id: string) => dirty.has(id) && dirty.get(id)! <= step;
  const recomputed = NODES.filter((n) => dirty.has(n.id) && n.kind !== "input").length;
  const total = NODES.filter((n) => n.kind !== "input").length;

  return (
    <div className="en-graph">
      <div className="en-graph-controls">
        <span className="subtle small">Send a tick:</span>
        {SYMS.map((s) => (
          <button key={s} type="button" className={`btn btn-sm ${src === `tick:${s}` ? "btn-primary" : ""}`} onClick={() => tick(`tick:${s}`)}>
            {s}
          </button>
        ))}
        <button type="button" className={`btn btn-sm ${src === "qty" ? "btn-primary" : ""}`} onClick={() => tick("qty")}>
          Trade (qty)
        </button>
        <button type="button" className={`btn btn-sm ${src === "alpha" ? "btn-primary" : ""}`} onClick={() => tick("alpha")}>
          Change α
        </button>
        <span className="en-graph-count small">
          <span className="num">{recomputed}</span> of <span className="num">{total}</span> computations re-run · <span className="num">{total - recomputed}</span> reused
        </span>
      </div>
      <div className="en-graph-scroll">
        <svg viewBox={`0 0 ${W} ${H}`} className="en-graph-svg" role="img" aria-label="Dependency graph: a tick propagates only to the nodes downstream of it">
          <defs>
            <marker id="en-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" className="en-arrow" />
            </marker>
            <marker id="en-arrow-lit" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" className="en-arrow-lit" />
            </marker>
          </defs>
          {EDGES.map(([a, b]) => {
            const p = pos[a];
            const q = pos[b];
            const x1 = p.x + NW;
            const y1 = p.y + NH / 2;
            const x2 = q.x;
            const y2 = q.y + NH / 2;
            const mx = (x1 + x2) / 2;
            const on = lit(a) && lit(b);
            return <path key={`${a}-${b}`} d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2 - 2},${y2}`} className={`en-edge ${on ? "lit" : ""}`} markerEnd={`url(#${on ? "en-arrow-lit" : "en-arrow"})`} />;
          })}
          {NODES.map((n) => {
            const p = pos[n.id];
            const on = lit(n.id);
            const clickable = n.kind === "input";
            return (
              <g key={n.id} transform={`translate(${p.x},${p.y})`} className={`en-node en-node-${n.kind} ${on ? "lit" : ""} ${dirty.has(n.id) ? "dirty" : ""} ${clickable ? "clickable" : ""}`} onClick={clickable ? () => tick(n.id) : undefined}>
                <rect width={NW} height={NH} rx={8} />
                <text x={10} y={15} className="en-node-label">
                  {n.label}
                </text>
                {n.sub && (
                  <text x={10} y={27} className="en-node-sub">
                    {n.sub}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
      <div className="en-legend small subtle">
        <span>
          <i className="en-swatch input" /> input (set from outside)
        </span>
        <span>
          <i className="en-swatch lit" /> recomputed on this change
        </span>
        <span>
          <i className="en-swatch idle" /> reused — its inputs did not change
        </span>
        <span className="en-legend-note">Illustration of the graph's shape for three symbols; the live engine has one such chain per holding.</span>
      </div>
    </div>
  );
}
