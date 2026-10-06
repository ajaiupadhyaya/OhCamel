/**
 * The Ledger (/ledger): what was built, what was cut, what is advisory, known limits, and
 * what the owner holds. Dated from a constant; each section a ruled table.
 */
import { DataTable, Page, Section, type Column } from "../components";
import { fmtStamp } from "../design/stamp";
import { LEDGER, LEDGER_AS_OF, SECTION_ORDER, type LedgerEntry } from "./ledger/entries";
import "./ledger/ledger.css";

type Row = LedgerEntry & { n: number };

const COLUMNS: Column<Row>[] = [
  { key: "n", label: "NO.", width: 48, render: (r) => <span className="num">{String(r.n).padStart(2, "0")}</span> },
  { key: "item", label: "ITEM", width: "30%", wrap: true, render: (r) => <span className="lg-item">{r.item}</span> },
  { key: "detail", label: "DETAIL", wrap: true },
];

export default function Ledger() {
  return (
    <Page
      title="Ledger"
      meta={
        <>
          <span>AS OF {fmtStamp(LEDGER_AS_OF)}</span>
          <span>{LEDGER.length} ENTRIES</span>
        </>
      }
    >
      {SECTION_ORDER.map((s) => {
        const rows = LEDGER.filter((e) => e.section === s).map((e, i) => ({ ...e, n: i + 1 }));
        return (
          <Section key={s} id={s.toLowerCase()} title={`${s} · ${rows.length}`}>
            <DataTable<Row> columns={COLUMNS} rows={rows} rowKey={(r) => `${s}-${r.n}`} />
          </Section>
        );
      })}
    </Page>
  );
}
