/**
 * A reading's or a tour step's text as React text: plain words, bold figures, and links to nodes. Everything here
 * comes from the wire and leaves as text children, never as HTML. A `ref` whose node is not in the graph is plain text.
 */
import { Fragment } from "react";
import type { Seg } from "./prose";

export interface SegsProps {
  segs: Seg[];
  has: (id: string) => boolean;
  onSelect: (id: string) => void;
}

export function Segs({ segs, has, onSelect }: SegsProps) {
  return (
    <>
      {segs.map((s, k) => {
        if ("b" in s) return <b key={k} className="mx-fig">{s.b}</b>;
        if ("ref" in s && has(s.ref)) {
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
