/**
 * The machine as a list (spec section 6.9): the same graph as a plain outline, for a browser that cannot draw it and
 * for anyone who would rather read it. By ring, outside in, each node a disclosure with its reading and what it reads
 * and feeds, the same search, and the same `?sel=` deep link. Plain HTML, no canvas, and nothing here reaches three.js:
 * the page loads the instrument only when it will draw one.
 *
 * A reading is written only for a node that is open, so 1,300 closed nodes cost one line each.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { MAX_CHIPS } from "./FocusPanel";
import { num } from "./fmt";
import { RADIUS } from "./layout";
import { reading } from "./readings";
import { Paras } from "./Segs";
import { hitName, TopBar } from "./TopBar";
import { stepView } from "./tour";
import type { Ring, Run } from "./types";
import { arrival, listSearch } from "./url";
import type { View } from "./url";
import { countKinds, nearestFirst, summaryLine, webOf } from "./web";
import type { GraphIndex } from "./web";

const RINGS: Record<Ring, string> = {
  feeds: "Feeds", daily: "Daily series", filings: "SEC filings", minute: "Minute bars", options: "Option chains", clock: "The clock",
  products: "Products", results: "Results", strategies: "Strategies", limits: "Limits", book: "The book",
};

/** The nodes by ring, in `RADIUS` order, outside in. A ring the page does not know comes last, so no node is lost. */
export function outline(gi: GraphIndex): { ring: string; word: string; nodes: number[] }[] {
  const order = Object.keys(RADIUS);
  const known = new Set(order);
  const byRing = new Map<string, number[]>([...order, "elsewhere"].map((ring) => [ring, []]));
  gi.out.nodes.forEach((n, i) => byRing.get(known.has(n.ring) ? n.ring : "elsewhere")!.push(i));
  return [...byRing].filter(([, nodes]) => nodes.length > 0).map(([ring, nodes]) => ({ ring, word: RINGS[ring as Ring] ?? "Elsewhere", nodes }));
}

const anchor = (i: number): string => `mx-node-${i}`;

export interface ListViewProps {
  gi: GraphIndex;
  view: View;
  runs: Run[];
  now: Date;
  /** Whether this browser can draw the machine. */
  drawable: boolean;
  /** Why the list is standing in for the machine, when it is. */
  note: string | null;
  onSelect(id: string | null): void;
}

export function ListView({ gi, view, runs, now, drawable, note, onSelect }: ListViewProps) {
  const rings = useMemo(() => outline(gi), [gi]);
  // the node the address names, as the machine would read it: a tour step's own node, or a `sel`
  const named = useMemo(() => arrival(view, stepView(gi, view.step, { runs, now })).view.sel, [gi, view, runs, now]);
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set(named === null ? [] : [named]));
  const [lastNamed, setLastNamed] = useState(named);
  if (named !== lastNamed) {
    setLastNamed(named);
    if (named !== null) setOpen(new Set(open).add(named));
  }

  /** The visitor opened this node where it stands, so the page need not take them to it. */
  const opened = useRef<string | null>(null);
  useEffect(() => {
    if (named === null) return;
    if (named === opened.current) {
      opened.current = null;
      return;
    }
    const el = document.getElementById(anchor(gi.byId.get(named) ?? -1));
    el?.scrollIntoView({ block: "start" });
    el?.querySelector("summary")?.focus({ preventScroll: true });
  }, [named, gi]);

  const toggle = (id: string, on: boolean): void => {
    if (on === open.has(id)) return;
    const next = new Set(open);
    if (on) next.add(id);
    else next.delete(id);
    setOpen(next);
    opened.current = on ? id : null;
    if (on) onSelect(id);
    else if (id === view.sel) onSelect(null);
  };

  return (
    <div className="mx-list">
      <TopBar gi={gi} now={now} view={view} onSelect={onSelect} drawable={drawable} />
      <div className="mx-list-body">
        <h1>The machine as a list</h1>
        {note && <p role="status">{note}</p>}
        <p className="mx-dim">
          {num(gi.out.nodes.length)} parts, from the outside in: what comes in, what is kept, the clock that runs it, what it makes, and the book at the centre.
        </p>
        <nav aria-label="Rings">
          <ul className="mx-rings">
            {rings.map(({ ring, word, nodes }) => (
              <li key={ring}>
                <a href={`#mx-ring-${ring}`}>
                  {word} <span>{num(nodes.length)}</span>
                </a>
              </li>
            ))}
          </ul>
        </nav>
        {rings.map(({ ring, word, nodes }) => (
          <section key={ring} aria-labelledby={`mx-ring-${ring}`}>
            <h2 id={`mx-ring-${ring}`}>
              {word} <span>{num(nodes.length)}</span>
            </h2>
            {nodes.map((i) => {
              const n = gi.out.nodes[i];
              const verdict = typeof n.m.verdict === "string" ? n.m.verdict : n.status === "missing" ? "MISSING" : null;
              const isOpen = open.has(n.id);
              return (
                <details key={n.id} id={anchor(i)} className="mx-node" open={isOpen} onToggle={(e) => toggle(n.id, e.currentTarget.open)}>
                  <summary>
                    {hitName(n)}
                    {verdict && <span className={/FAIL/.test(verdict) ? "fail" : undefined}>{verdict}</span>}
                  </summary>
                  {isOpen && <Reading gi={gi} i={i} view={view} runs={runs} now={now} onSelect={onSelect} />}
                </details>
              );
            })}
          </section>
        ))}
      </div>
    </div>
  );
}

function NodeLink({ id, view, children, className }: { id: string; view: View; children: string; className: string }) {
  return (
    <Link className={className} to={{ search: listSearch(view, id) }}>
      {children}
    </Link>
  );
}

function Reading({ gi, i, view, runs, now, onSelect }: Pick<ListViewProps, "gi" | "view" | "runs" | "now" | "onSelect"> & { i: number }) {
  const read = useMemo(() => reading(gi, i, { runs, now }), [gi, i, runs, now]);
  const web = useMemo(() => webOf(gi, i, "web"), [gi, i]);
  return (
    <div className="mx-node-body">
      <h3>{read.title}</h3>
      <Paras
        paras={read.paras}
        has={(id) => gi.byId.has(id)}
        onSelect={onSelect}
        link={(id, text) => (
          <NodeLink id={id} view={view} className="mx-tok">
            {text}
          </NodeLink>
        )}
      />
      {read.caveats.length > 0 && (
        <>
          <h4 className="mx-h3">notes it writes about itself</h4>
          <ul className="mx-notes">
            {read.caveats.map((c, k) => (
              <li key={k}>{c}</li>
            ))}
          </ul>
        </>
      )}
      <Side gi={gi} view={view} tone="up" word="reads" web={web.up} />
      <Side gi={gi} view={view} tone="down" word="feeds" web={web.down} />
    </div>
  );
}

function Side({ gi, view, tone, word, web }: { gi: GraphIndex; view: View; tone: string; word: string; web: Map<number, number> }) {
  if (web.size === 0) return null;
  const { shown, more } = nearestFirst(gi, web, MAX_CHIPS);
  return (
    <>
      <h4 className="mx-h3">
        <i className={`mx-sw ${tone}`} aria-hidden="true" />
        {word} <b>{summaryLine(countKinds(gi, web.keys()))}</b>
      </h4>
      <ul className="mx-chips">
        {shown.map((j) => (
          <li key={j}>
            <NodeLink id={gi.out.nodes[j].id} view={view} className="mx-chip">
              {gi.out.nodes[j].l}
            </NodeLink>
          </li>
        ))}
        {more > 0 && <li className="mx-chip more">+{num(more)} more</li>}
      </ul>
    </>
  );
}
