/**
 * The Ledger (/ledger): what was built, what was cut, what is advisory, known limits, and what the owner holds,
 * read from GET /api/ledger. Dated by the server on the day it is built; each section a ruled table. A prose row
 * says when it was last checked and is hatched once that is past the server's stale age; a computed row says
 * whether its source read ok, needs attention or is unknown, and where it was read. A product row also reads its
 * kind's latest artifact and shows its verdict and data date, or NOT YET RUN. When the ledger cannot be read the
 * page says so; there is no copy in the bundle to fall back on.
 */
import { Link } from "react-router-dom";
import { DataTable, Page, Section, type Column } from "../components";
import { Absent } from "../design";
import { fmtStamp } from "../design/stamp";
import { manifestOf, useArtifact } from "../lib/artifacts";
import { useApiQuery } from "../lib/query";
import { bySection, ledgerAbsent, rowMark, type LedgerOut, type NumberedRow } from "./ledger/ledger";
import { productStatus } from "./ledger/status";
import "./ledger/ledger.css";

function ProductState({ kind }: { kind: string }) {
  const q = useArtifact(kind);
  const s = productStatus(manifestOf(q.data), q.error, q.isLoading);
  return <span className={"lg-state num" + (s.fail ? " lg-state-fail" : "")}>{s.text}</span>;
}

const hatch = (r: NumberedRow) => (r.stale ? "lg-cell lg-hatch" : "lg-cell");

const COLUMNS: Column<NumberedRow>[] = [
  { key: "n", label: "NO.", width: 48, render: (r) => <span className="num">{String(r.n).padStart(2, "0")}</span> },
  { key: "item", label: "ITEM", width: "30%", wrap: true, render: (r) => {
      const mark = rowMark(r);
      return (
        <div className={hatch(r)}>
          {r.methodology ? (
            <Link className="lg-item lg-link" to={`/methodology#${r.methodology}`}>{r.item}</Link>
          ) : (
            <span className={"lg-item" + (r.state === "attention" ? " lg-item-attention" : "")}>{r.item}</span>
          )}
          {r.kind && <ProductState kind={r.kind} />}
          <span className={mark.cls}>{mark.text}</span>
        </div>
      );
    } },
  { key: "detail", label: "DETAIL", wrap: true, render: (r) => <div className={hatch(r)}>{r.detail}</div> },
];

/** The sections, given a ledger the server built. */
export function LedgerSections({ data }: { data: LedgerOut }) {
  return (
    <>
      {bySection(data.rows).map(({ section, rows }) => (
        <Section key={section} id={section.toLowerCase()} title={`${section} · ${rows.length}`}>
          {rows.length ? (
            <DataTable<NumberedRow> columns={COLUMNS} rows={rows} rowKey={(r) => `${section}-${r.n}`} />
          ) : (
            <Absent reason="NO ROWS" source={`GET /api/ledger · ${section}`} />
          )}
        </Section>
      ))}
    </>
  );
}

export default function Ledger() {
  const q = useApiQuery<LedgerOut>("/ledger", undefined, { staleTime: 60_000 });
  const d = q.data;
  return (
    <Page
      title="Ledger"
      meta={
        <>
          <span>AS OF {d ? fmtStamp(d.as_of) : "—"}</span>
          <span>{d ? `${d.rows.length} ENTRIES` : "ENTRIES —"}</span>
          {d && <span>STALE AFTER {d.stale_after_days} DAYS</span>}
        </>
      }
    >
      {d ? (
        <LedgerSections data={d} />
      ) : q.isError ? (
        <Absent reason={ledgerAbsent(q.error)} source="GET /api/ledger" />
      ) : (
        <Absent reason="READING" source="GET /api/ledger" />
      )}
    </Page>
  );
}
