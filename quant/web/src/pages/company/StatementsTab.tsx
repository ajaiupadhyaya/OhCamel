/**
 * STATEMENTS — GET /api/fundamentals/{t}/statements: one dense ruled table per statement, one
 * row per standardized line item, periods as columns (oldest → newest), a trend line, and with
 * YOY on a growth row under each item. USD millions except per-share figures; outflows in
 * parentheses; items the filings do not report are listed, never imputed.
 */
import { Fragment, useMemo } from "react";
import { Panel, SegmentedControl, Sparkline, Toggle, useTabParam } from "../../components";
import { Note } from "../../design";
import { EM_DASH, fmtNum, fmtPct, signClass } from "../../lib/format";
import { Controls, Ctl, periodLabel, useStatements } from "./shared";
import type { Period, StatementTable, Statements } from "./types";

/** Subtotals set in bold with a rule above. */
const KEY_ROWS = new Set(["revenue", "gross_profit", "operating_income", "net_income", "total_assets", "total_liabilities", "equity", "cfo", "fcf"]);
/** Outflows, shown in parentheses when positive. */
export const OUTFLOWS = new Set(["cost_of_revenue", "sga", "rnd", "interest_expense", "income_tax", "capex", "dividends_paid", "buybacks", "d_and_a"]);

const SECTIONS: { key: "income_statement" | "balance_sheet" | "cash_flow"; title: string }[] = [
  { key: "income_statement", title: "INCOME STATEMENT" },
  { key: "balance_sheet", title: "BALANCE SHEET" },
  { key: "cash_flow", title: "CASH FLOW" },
];

const PERIODS: { value: Period; label: string; title: string }[] = [
  { value: "annual", label: "ANNUAL", title: "Fiscal years (10-K)" },
  { value: "quarterly", label: "QUARTERLY", title: "Fiscal quarters (10-Q; Q4 = FY − 9M)" },
  { value: "ttm", label: "TTM", title: "Rolling four quarters, one column per quarter end" },
];

/** A statement figure in USD millions (EPS in dollars); outflows in parentheses. */
export function fmtCell(v: number | null, item: string): string {
  if (v == null || !Number.isFinite(v)) return EM_DASH;
  const eps = item === "eps_diluted";
  const x = eps ? v : v / 1e6;
  const dg = eps ? 2 : Math.abs(x) < 10 ? 1 : 0;
  const s = fmtNum(Math.abs(x), dg);
  if (OUTFLOWS.has(item) && x > 0) return `(${s})`;
  return x < 0 ? `−${s}` : s;
}

export function StatementsTab({ ticker }: { ticker: string }) {
  const [period, setPeriod] = useTabParam<Period>("period", "annual");
  const [yoyParam, setYoy] = useTabParam<"1" | "0">("yoy", "0");
  const showYoy = yoyParam === "1";
  const q = useStatements(ticker, period, period === "annual" ? 10 : 12);
  const d = q.data;
  const last = d?.income_statement.periods.at(-1);

  return (
    <div className="stack">
      <Controls right={<>USD M · EPS $ · SHARES M</>}>
        <Ctl label="PERIOD">
          <SegmentedControl size="sm" ariaLabel="Statement frequency" options={PERIODS} value={period} onChange={setPeriod} />
        </Ctl>
        <Toggle label="YOY" checked={showYoy} onChange={(v) => setYoy(v ? "1" : "0")} />
      </Controls>
      {SECTIONS.map((s, i) => (
        <Panel<Statements>
          key={s.key}
          title={
            <>
              {s.title} · {period === "annual" ? "FY" : period === "ttm" ? "TTM" : "QTR"}
              {i === 0 && <Note n={1} to="statements" />}
            </>
          }
          query={q}
          flush
          skeletonHeight={300}
          asOf={last}
          notes={i === SECTIONS.length - 1 ? undefined : []}
          provenance={i === SECTIONS.length - 1 ? undefined : []}
        >
          {(dd) => <FinTable table={dd[s.key]} period={dd.period} showYoy={showYoy} label={s.title} />}
        </Panel>
      ))}
    </div>
  );
}

function FinTable({ table, period, showYoy, label }: { table: StatementTable; period: Period; showYoy: boolean; label: string }) {
  const rows = useMemo(() => table.rows.filter((r) => r.available), [table]);
  const missing = table.rows.filter((r) => !r.available).map((r) => r.label);
  const n = table.periods.length;
  return (
    <>
      <div className="oc-table-wrap" tabIndex={0} role="region" aria-label={label}>
        <table className="oc-table oc-table-compact co-fin">
          <thead>
            <tr>
              <th className="co-fin-item">ITEM</th>
              <th className="co-fin-spark">TREND</th>
              {table.periods.map((p, j) => (
                <th key={p} className={`num ${j === n - 1 ? "co-fin-latest" : ""}`} title={p}>
                  {periodLabel(p, period)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <Fragment key={r.item}>
                <tr className={KEY_ROWS.has(r.item) ? "co-fin-key" : ""}>
                  <td className="co-fin-item">{r.label}</td>
                  <td className="co-fin-spark">
                    <Sparkline values={r.values} width={64} height={16} area={false} strokeWidth={1} color={OUTFLOWS.has(r.item) ? "var(--ink-2)" : "var(--ink)"} />
                  </td>
                  {r.values.map((v, j) => (
                    <td key={j} className={`num ${j === n - 1 ? "co-fin-latest" : ""}`}>
                      {fmtCell(v, r.item)}
                    </td>
                  ))}
                </tr>
                {showYoy && (
                  <tr className="co-fin-yoy">
                    <td className="co-fin-item">YOY</td>
                    <td className="co-fin-spark" />
                    {r.values.map((_, j) => {
                      const g = r.yoy?.[j] ?? null;
                      return (
                        <td key={j} className={`num ${g == null ? "" : signClass(g, OUTFLOWS.has(r.item))}`}>
                          {g == null ? "" : fmtPct(g, 1, { signed: true })}
                        </td>
                      );
                    })}
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      {missing.length > 0 && <div className="co-fin-missing num">NOT REPORTED · {missing.join(" · ").toUpperCase()}</div>}
    </>
  );
}
