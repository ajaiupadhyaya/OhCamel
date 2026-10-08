/**
 * Plumbing for the product views (Farm, Models): the latest manifest of a kind without its
 * own Cell, one artifact table with its absent / loading / failed states, and a Cell that
 * renders only once the product exists (the lead ArtifactCell already says NOT YET RUN).
 */
import type { ReactNode } from "react";
import { Panel, Skeleton } from "../../components";
import { Absent } from "../../design";
import { artifactMaxAge, artifactStamp, manifestOf, useArtifact, useArtifactTable, type Manifest } from "../../lib/artifacts";

export function useLatest(kind: string): Manifest | null {
  const q = useArtifact(kind);
  return manifestOf(q.data);
}

/** One table of an artifact: rows, or the reason there are none (as a rendered block). */
export function useTable<T>(kind: string, m: Manifest | null, table: string): { rows: T[] | null; block: ReactNode } {
  const listed = !m?.tables || m.tables.includes(table);
  const q = useArtifactTable(kind, m?.id, table, !!m && listed);
  if (!m) return { rows: null, block: null };
  const src = `${kind}/${m.id}/${table}`;
  if (!listed) return { rows: null, block: <Absent reason={`NO ${table.toUpperCase()} TABLE`} source={src} /> };
  if (q.isLoading) return { rows: null, block: <Skeleton height={120} /> };
  if (q.isError) return { rows: null, block: <Absent reason="TABLE UNAVAILABLE" source={src} /> };
  if (!q.data) return { rows: null, block: <Absent reason="UNKNOWN TABLE SHAPE" source={src} /> };
  return { rows: q.data as T[], block: null };
}

/** A Cell over an existing artifact: its asOf, stale mark and provenance; nothing before it exists. */
export function ProductPanel({ m, title, children, flush, span }: { m: Manifest | null; title: ReactNode; children: ReactNode; flush?: boolean; span?: 2 | "all" }) {
  if (!m) return null;
  const stamp = artifactStamp(m);
  return (
    <Panel title={title} asOf={stamp.at} asOfLabel={stamp.label} maxAgeSec={artifactMaxAge(m)} provenance={m.provenance} flush={flush} span={span}>
      {children}
    </Panel>
  );
}

const num = (x: unknown): number | null => (typeof x === "number" && Number.isFinite(x) ? x : null);
const str = (x: unknown): string | null => (typeof x === "string" ? x : null);

/** Coerce a record's fields: numbers stay numbers, everything else null. */
export function pick<T>(r: Record<string, unknown>, nums: string[], strs: string[] = [], rest: Record<string, (x: unknown) => unknown> = {}): T {
  const out: Record<string, unknown> = {};
  for (const k of nums) out[k] = num(r[k]);
  for (const k of strs) out[k] = str(r[k]);
  for (const [k, f] of Object.entries(rest)) out[k] = f(r[k]);
  return out as T;
}

export const asBool = (x: unknown): boolean | null => (typeof x === "boolean" ? x : x === 1 || x === "true" ? true : x === 0 || x === "false" ? false : null);
