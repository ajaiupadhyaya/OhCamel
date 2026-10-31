/**
 * What each kind of input recomputes, replayed from GET /api/engine/propagation: a recording
 * of the real graph on an example book of real closes (`make propagation`), never the live
 * book and never a simulation. Pick an input and the figure lights what ran there, rank by
 * rank.
 *
 * Paper Tape: ink only, state carried by shape as well as weight. A filled square moved, a
 * hollow square ran and its cutoff held, a faint one was reused. Every drawn node and edge is
 * Incremental's own. The lists under the figure are the same facts for a phone or a reader.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { DataTable, Panel, SegmentedControl, StatGrid, StatTile, type Column } from "../../components";
import { fmtMultiple, fmtNum } from "../../lib/format";
import type { RecordedEvent, Recording, ScalingRow } from "./types";
import { edgeLit, fmtNs, layoutGraph, nodeStates, reading, tally } from "./replay";

const KINDS = [
  { value: "tick", label: "RISE" },
  { value: "fall", label: "FALL" },
  { value: "repeat", label: "SAME PRICE" },
  { value: "fill", label: "FILL" },
  { value: "roll", label: "NEW DAY" },
  { value: "factor", label: "FACTOR" },
  { value: "cash", label: "CASH" },
] as const;
type Kind = (typeof KINDS)[number]["value"];

const STEP_MS = 110;
const SQUARE = 7;

function find(rec: Recording, kind: Kind, symbol: string): RecordedEvent | undefined {
  return rec.events.find((e) => e.kind === kind && (e.symbol == null || e.symbol === symbol));
}

export function Propagation({ rec }: { rec: Recording }) {
  const [kind, setKind] = useState<Kind>("tick");
  const [symbol, setSymbol] = useState(rec.book.positions[0]?.symbol ?? "");
  const [hover, setHover] = useState<string | null>(null);
  const ev = find(rec, kind, symbol);
  const layout = useMemo(() => layoutGraph(rec), [rec]);
  const states = useMemo(() => (ev ? nodeStates(rec.graph.nodes, ev) : new Map()), [rec, ev]);
  const maxRank = useMemo(() => Math.max(...rec.graph.nodes.map((n) => n.rank)), [rec]);

  // The replay: one rank at a time, as Incremental runs them. Reduced motion shows it whole.
  const [step, setStep] = useState(maxRank);
  const still = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  useEffect(() => setStep(still ? maxRank : 0), [ev?.id, still, maxRank]);
  useEffect(() => {
    if (step >= maxRank) return;
    const t = window.setTimeout(() => setStep((s) => s + 1), STEP_MS);
    return () => window.clearTimeout(t);
  }, [step, maxRank]);

  if (!ev) return null;
  const t = tally(rec, ev);
  const shown = (name: string) => (layout.placed.get(name)?.node.rank ?? 0) <= step;
  const focus = hover ? layout.placed.get(hover)?.node : undefined;
  const neighbours = hover ? rec.graph.edges.filter(([a, b]) => a === hover || b === hover) : [];

  return (
    <div className="en-graph">
      <div className="en-graph-controls">
        <SegmentedControl size="sm" ariaLabel="Kind of input" options={[...KINDS]} value={kind} onChange={setKind} />
        {ev.symbol != null && (
          <div className="en-graph-names" role="group" aria-label="Which name">
            {rec.book.positions.map((p) => (
              <button key={p.symbol} type="button" className={`btn btn-sm ${symbol === p.symbol ? "btn-primary" : ""}`} aria-pressed={symbol === p.symbol} onClick={() => setSymbol(p.symbol)}>
                {p.symbol}
              </button>
            ))}
          </div>
        )}
      </div>

      <StatGrid min={120}>
        <StatTile size="sm" label="RAN" value={`${t.ran} / ${t.named}`} caption="NAMED BODIES" />
        <StatTile size="sm" label="MOVED" value={fmtNum(t.changed, 0)} caption="VALUE CHANGED" />
        <StatTile size="sm" label="HELD" value={fmtNum(t.held.length, 0)} caption="CUTOFF STOPPED IT" />
        <StatTile size="sm" label="REUSED" value={fmtNum(t.reused, 0)} caption="NOT RECOMPUTED" />
        <StatTile size="sm" label="STABILIZE" value={fmtNs(ev.stabilize_ns)} caption={`MEDIAN · ${fmtNum(rec.samples, 0)} RUNS`} />
      </StatGrid>

      <p className="en-reading">{reading(rec, ev)}</p>

      <div className="en-graph-scroll" tabIndex={0} role="region" aria-label="The dependency graph, scrolls sideways">
        <svg viewBox={`0 0 ${layout.width} ${layout.height}`} className="en-graph-svg" role="img" aria-label={reading(rec, ev)} onMouseLeave={() => setHover(null)}>
          {rec.graph.edges.map(([a, b]) => {
            const p = layout.placed.get(a);
            const q = layout.placed.get(b);
            if (!p || !q) return null;
            const x1 = p.x + SQUARE;
            const x2 = q.x;
            const mx = (x1 + x2) / 2;
            const lit = edgeLit(states, a, b) && shown(b);
            const near = hover === a || hover === b;
            return <path key={`${a}>${b}`} d={`M${x1},${p.y} C${mx},${p.y} ${mx},${q.y} ${x2},${q.y}`} className={`en-flow ${lit ? "lit" : ""} ${near ? "near" : ""}`} />;
          })}
          {[...layout.placed.values()].map(({ node, x, y }) => {
            const state = shown(node.name) ? (states.get(node.name) ?? "idle") : "idle";
            return (
              <g key={node.name} transform={`translate(${x},${y})`} className={`en-cell ${state}`} onMouseEnter={() => setHover(node.name)}>
                <rect x={-3} y={-7} width={node.name.length * 5.7 + SQUARE + 10} height={14} className="en-cell-hit" />
                <rect y={-SQUARE / 2} width={SQUARE} height={SQUARE} rx={0} ry={0} shapeRendering="crispEdges" className="en-cell-mark" />
                <text x={SQUARE + 4} y={3}>
                  {node.name}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      <div className="en-legend num">
        <span>
          <i className="en-swatch moved" /> MOVED
        </span>
        <span>
          <i className="en-swatch held" /> RAN · CUTOFF HELD
        </span>
        <span>
          <i className="en-swatch idle" /> REUSED
        </span>
        <span className="en-legend-focus">
          {focus
            ? `${focus.name} · ${focus.kind}${focus.cost ? ` · ${focus.cost}` : ""} · CUTOFF ${focus.cutoff} · READS ${neighbours.filter(([, b]) => b === hover).length} · FEEDS ${neighbours.filter(([a]) => a === hover).length}`
            : "POINT AT A NODE"}
        </span>
      </div>

      <dl className="en-lists">
        <List term="SET" names={ev.changed.filter((n) => states.get(n) === "input")} />
        <List term="RAN AND MOVED" names={ev.ran.filter((n) => states.get(n) === "changed")} />
        <List term="RAN AND HELD" names={t.held} />
      </dl>
    </div>
  );
}

function List({ term, names }: { term: string; names: string[] }) {
  return (
    <div>
      <dt>{term}</dt>
      <dd className="num">{names.length ? names.join(" · ") : "NONE"}</dd>
    </div>
  );
}

const ROWS: Column<ScalingRow>[] = [
  { key: "instruments", label: "NAMES", numeric: true, format: (v) => fmtNum(v as number, 0) },
  { key: "named_nodes", label: "BODIES IN THE GRAPH", numeric: true, format: (v) => fmtNum(v as number, 0) },
  { key: "nodes_per_tick", label: "RAN PER TICK", numeric: true, format: (v) => fmtNum(v as number, 1) },
  { key: "incremental_ns", label: "INCREMENTAL", numeric: true, format: (v) => fmtNs(v as number) },
  { key: "full_ns", label: "FULL RECOMPUTE", numeric: true, format: (v) => fmtNs(v as number) },
  { key: "ratio", label: "FULL / INCREMENTAL", numeric: true, render: (r) => <span className="num">{fmtMultiple(r.full_ns / r.incremental_ns, 0)}</span> },
];

/** The benchmark's own book as it grows; the cost of a tick against recomputing everything. */
export function Scaling({ rec, title }: { rec: Recording; title: ReactNode }) {
  return (
    <Panel title={title} flush notes={[`${rec.scaling_book}. A median price change; a tick from the feed also runs that name's feed nodes, as the figure above shows. The full recompute is the baseline in bench/baseline.ml, using the same pure functions.`]} provenance={[]}>
      <DataTable compact columns={ROWS} rows={rec.scaling} rowKey={(r) => String(r.instruments)} scrollLabel="Tick cost by book size" />
    </Panel>
  );
}
