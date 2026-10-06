/**
 * The provenance line, mono: "SOURCE fred DGS10 · FETCHED 05 OCT 16:02".
 * Records with the same source are merged; groups are separated by " / ".
 * Hover a source for its full detail.
 */
import type { ProvenanceRecord } from "../lib/types";
import { fmtStamp } from "../design/stamp";

function symbolsOf(p: ProvenanceRecord): string[] {
  const d = p.detail ?? {};
  const raw = (d as any).symbols ?? (d as any).symbol ?? (d as any).tickers ?? (d as any).ticker ?? (d as any).series;
  if (Array.isArray(raw)) return raw.map(String);
  if (typeof raw === "string") return [raw];
  return [];
}

export function Provenance({ items, className }: { items?: ProvenanceRecord[] | null; className?: string }) {
  if (!items || !items.length) return null;
  const groups = new Map<string, { latest: string; symbols: Set<string>; details: ProvenanceRecord[] }>();
  for (const p of items) {
    if (!p || !p.source) continue;
    const g = groups.get(p.source) ?? { latest: p.fetched_at, symbols: new Set<string>(), details: [] };
    if (p.fetched_at && (!g.latest || p.fetched_at > g.latest)) g.latest = p.fetched_at;
    symbolsOf(p).forEach((s) => g.symbols.add(s));
    g.details.push(p);
    groups.set(p.source, g);
  }
  if (!groups.size) return null;
  return (
    <div className={`oc-provenance num ${className ?? ""}`}>
      <span className="oc-provenance-label">SOURCE</span>
      {[...groups].map(([src, g], i) => {
        const syms = [...g.symbols];
        const title = g.details
          .map((d) => `${d.source} · fetched ${d.fetched_at}${d.detail && Object.keys(d.detail).length ? "\n" + JSON.stringify(d.detail) : ""}`)
          .join("\n\n");
        return (
          <span key={src} className="oc-provenance-item" title={title}>
            {i > 0 && <span className="oc-provenance-sep">/</span>}
            <span className="oc-provenance-src">{src}</span>
            {syms.length > 0 && <span> {syms.length > 6 ? `${syms.slice(0, 6).join(" ")} +${syms.length - 6}` : syms.join(" ")}</span>}
            {g.latest && (
              <span>
                {" · "}FETCHED <time dateTime={g.latest}>{fmtStamp(g.latest)}</time>
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}
