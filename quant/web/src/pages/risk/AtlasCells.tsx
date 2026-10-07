/**
 * ATLAS (P1, risk.mc_atlas): nightly Monte Carlo VaR/ES for the reference books and universe
 * members, FHS against a Student-t copula, 1D and 10D. Two Cells over one artifact: the
 * summary pivot (FHS beside t-copula for the chosen α and horizon) and the Euler ES split of
 * one book. Before the product has run both read INSUFFICIENT DATA · NOT YET RUN.
 */
import { useState } from "react";
import { ArtifactCell, DataTable, SegmentedControl, Select, Skeleton, type Column } from "../../components";
import { Absent } from "../../design";
import { KINDS, useArtifactTable, type Manifest } from "../../lib/artifacts";
import { fmtNum, fmtPct } from "../../lib/format";
import { ShareRows } from "../portfolio/shared";
import { atlasBooks, atlasRows, eulerRows, type AtlasBook } from "./atlas";

type Alpha = "0.95" | "0.99";
type H = "1" | "10";

export function Atlas({ seedAlpha, seedH }: { seedAlpha: Alpha; seedH: H }) {
  const [alpha, setAlpha] = useState<Alpha>(seedAlpha);
  const [h, setH] = useState<H>(seedH);
  const controls = (
    <>
      <SegmentedControl size="sm" ariaLabel="Atlas confidence" options={[{ value: "0.95", label: "95" }, { value: "0.99", label: "99" }]} value={alpha} onChange={setAlpha} />
      <SegmentedControl size="sm" ariaLabel="Atlas horizon" options={[{ value: "1", label: "1D" }, { value: "10", label: "10D" }]} value={h} onChange={setH} />
    </>
  );
  return (
    <div className="grid-3">
      <ArtifactCell title={`Atlas · MC VaR ${fmtNum(Number(alpha) * 100, 0)} · ${h}D`} kind={KINDS.atlas} span={2} controls={controls}>
        {(m) => <AtlasSummary m={m} alpha={Number(alpha)} h={Number(h)} />}
      </ArtifactCell>
      <ArtifactCell title={`Atlas · Euler ES · ${h}D`} kind={KINDS.atlas}>
        {(m) => <AtlasEuler m={m} alpha={Number(alpha)} h={Number(h)} />}
      </ArtifactCell>
    </div>
  );
}

function useTable(m: Manifest, table: string) {
  return useArtifactTable(KINDS.atlas, m.id, table, !m.tables || m.tables.includes(table));
}

function TableState({ m, table, q }: { m: Manifest; table: string; q: ReturnType<typeof useTable> }) {
  if (m.tables && !m.tables.includes(table)) return <Absent reason={`NO ${table.toUpperCase()} TABLE`} source={`${KINDS.atlas}/${m.id}`} />;
  if (q.isLoading) return <Skeleton height={120} />;
  if (q.isError) return <Absent reason="TABLE UNAVAILABLE" source={`${KINDS.atlas}/${m.id}/${table}`} />;
  return null;
}

function AtlasSummary({ m, alpha, h }: { m: Manifest; alpha: number; h: number }) {
  const q = useTable(m, "summary");
  const blocked = TableState({ m, table: "summary", q });
  if (blocked) return blocked;
  const rows = atlasRows(q.data);
  if (!rows) return <Absent reason="UNKNOWN TABLE SHAPE" source={`${KINDS.atlas}/${m.id}/summary`} />;
  const books = atlasBooks(rows, alpha, h);
  if (!books.length) return <Absent reason={`NO RUN AT ${fmtNum(alpha * 100, 0)} · ${h}D`} source={`${KINDS.atlas}/${m.id}/summary`} />;
  const paths = rows.find((r) => r.nPaths != null)?.nPaths;
  const cols: Column<AtlasBook>[] = [
    { key: "book", label: "Book", render: (r) => <span className="num">{r.book.toUpperCase()}</span> },
    { key: "fhs_var", label: "FHS VaR", numeric: true, value: (r) => r.fhs?.var ?? null, format: (v) => fmtPct(v, 2), info: "var" },
    { key: "fhs_es", label: "FHS ES", numeric: true, value: (r) => r.fhs?.es ?? null, format: (v) => fmtPct(v, 2), info: "es" },
    { key: "t_var", label: "t-cop VaR", numeric: true, value: (r) => r.tcop?.var ?? null, format: (v) => fmtPct(v, 2) },
    { key: "t_es", label: "t-cop ES", numeric: true, value: (r) => r.tcop?.es ?? null, format: (v) => fmtPct(v, 2), hideBelow: 600 },
    { key: "gap", label: "Δ VaR", numeric: true, value: (r) => r.gap, format: (v) => fmtPct(v, 2, { signed: true }), info: { text: "t-copula VaR minus FHS VaR." } },
  ];
  return (
    <>
      {paths != null && <div className="pl-meta num">PATHS {fmtNum(paths, 0)} · {books.length} BOOKS</div>}
      <DataTable rows={books} columns={cols} rowKey={(r) => r.book} defaultSort={{ key: "fhs_var", dir: "desc" }} compact />
    </>
  );
}

function AtlasEuler({ m, alpha, h }: { m: Manifest; alpha: number; h: number }) {
  const sq = useTable(m, "summary");
  const eq = useTable(m, "euler");
  const [book, setBook] = useState<string | null>(null);
  const blocked = TableState({ m, table: "euler", q: eq });
  if (blocked) return blocked;
  const books = [...new Set((atlasRows(sq.data) ?? []).map((r) => r.book))];
  const shown = book ?? books[0] ?? (eq.data?.[0]?.book as string | undefined) ?? "";
  const rows = eulerRows(eq.data, shown, { method: "fhs", alpha, horizon: h });
  if (!rows) return <Absent reason="UNKNOWN TABLE SHAPE" source={`${KINDS.atlas}/${m.id}/euler`} />;
  return (
    <>
      {books.length > 1 && <Select ariaLabel="Atlas book" value={shown} onChange={setBook} options={books.map((b) => ({ value: b, label: b.toUpperCase() }))} />}
      {rows.length ? <ShareRows rows={rows} riskLabel="ES SHARE" /> : <Absent reason={`NO EULER ROWS · ${shown.toUpperCase()}`} source={`${KINDS.atlas}/${m.id}/euler`} />}
    </>
  );
}
