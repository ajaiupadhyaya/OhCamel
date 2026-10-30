/**
 * A reading's or a tour step's text as React text: plain words, bold figures, and links to nodes. Everything here
 * comes from the wire and leaves as text children, never as HTML. A `ref` whose node is not in the graph is plain text.
 */
import { Fragment, type ReactNode } from "react";
import type { Seg } from "./prose";

export interface SegsProps {
  segs: Seg[];
  has: (id: string) => boolean;
  onSelect: (id: string) => void;
  /** Writes a node's reference as a link, where the page has addresses to offer: the list does, the panels select in place. */
  link?: (id: string, text: string) => ReactNode;
}

export function Segs({ segs, has, onSelect, link }: SegsProps) {
  return (
    <>
      {segs.map((s, k) => {
        if ("b" in s) return <b key={k} className="mx-fig">{s.b}</b>;
        if ("ref" in s && has(s.ref)) {
          if (link) return <Fragment key={k}>{link(s.ref, s.t)}</Fragment>;
          return (
            <button key={k} type="button" className="mx-tok" onClick={() => onSelect(s.ref)}>
              {s.t}
            </button>
          );
        }
        return <Fragment key={k}>{s.t}</Fragment>;
      })}
    </>
  );
}

/** Paragraphs of segments. */
export function Paras({ paras, ...rest }: Omit<SegsProps, "segs"> & { paras: Seg[][] }) {
  return (
    <>
      {paras.map((p, k) => (
        <p key={k}>
          <Segs segs={p} {...rest} />
        </p>
      ))}
    </>
  );
}
