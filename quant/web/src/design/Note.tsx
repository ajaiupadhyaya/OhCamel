/**
 * A footnote marker: a superscript number linking to a Methodology entry.
 *   VaR 99 · 1D<Note n={1} to="var-fhs" />
 */
export function Note({ n, to }: { n: number; to: string }) {
  return (
    <sup className="note">
      <a href={"/methodology#" + to} aria-label={`Note ${n}: methodology ${to}`}>
        {n}
      </a>
    </sup>
  );
}
