/**
 * Table: 22px rows, faint inner rules under a heavy header rule, mono right-aligned numbers,
 * a sticky header (set `maxHeight` to scroll inside the table) and a sticky first column.
 * The table scrolls sideways inside its own wrapper; the page never does.
 * With `onRowClick`, rows take a roving focus: ↑/↓ move the active row, Enter opens it.
 *
 *   <DataTable
 *     rows={rows}
 *     rowKey={(r) => r.ticker}
 *     onRowClick={(r) => navigate(`/ticker/${r.ticker}`)}
 *     defaultSort={{ key: "ret_1d", dir: "desc" }}
 *     columns={[
 *       { key: "ticker", label: "Ticker", render: (r) => <strong>{r.ticker}</strong> },
 *       { key: "ret_1d", label: "1D", numeric: true, format: fmtSignedPct, color: "sign" },
 *       { key: "vol", label: "Vol", numeric: true, format: (v) => fmtPct(v, 1), info: "vol" },
 *       { key: "w", label: "Weight", numeric: true, format: fmtPct, heat: { min: 0, max: 0.3 } },
 *     ]}
 *   />
 *
 * Columns are numeric-aligned (right, mono, tabular) when `numeric` is set.
 * `color: "sign"` sets gains in ink and losses in --signal; a function may return any CSS
 * colour or class. `heat` shades the cell on the diverging (signal ↔ ink) or the sequential
 * ink-density ramp (--seq-1 … --seq-5); dark buckets switch the text to paper.
 * `wrap` lets a long-text column (e.g. references) wrap; give it a `width` to bound it.
 * `hideBelow` hides a column under a viewport width (600 / 900 / 1200 / 1440 / 1600).
 */
import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { EM_DASH } from "../lib/format";
import { InfoTip, type InfoProp } from "./InfoTip";

export interface Column<T> {
  key: string;
  label: ReactNode;
  numeric?: boolean;
  /** Read the cell value (default: row[key]). Used for sorting and `format`. */
  value?: (row: T) => unknown;
  format?: (v: any, row: T) => ReactNode;
  /** Full custom cell renderer (overrides format). */
  render?: (row: T) => ReactNode;
  sortable?: boolean;
  width?: number | string;
  align?: "left" | "right" | "center";
  color?: "sign" | "sign-inverted" | ((v: any, row: T) => string | undefined);
  heat?: { min?: number; max?: number; diverging?: boolean };
  info?: InfoProp;
  title?: string;
  /**
   * Hide below this viewport width (px) — keeps mobile tables readable. Snaps up to the
   * nearest breakpoint: 600, 900, 1200, 1440, 1600.
   */
  hideBelow?: number;
  /** Let long text (references, descriptions) wrap instead of the default single line. */
  wrap?: boolean;
  className?: string;
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey?: (row: T, i: number) => string | number;
  onRowClick?: (row: T) => void;
  defaultSort?: { key: string; dir: "asc" | "desc" };
  compact?: boolean;
  maxHeight?: number | string;
  empty?: ReactNode;
  caption?: ReactNode;
  className?: string;
  footer?: ReactNode;
  /** Highlight a row (e.g. the selected ticker). */
  isActive?: (row: T) => boolean;
}

const HIDE_BREAKPOINTS = [600, 900, 1200, 1440, 1600] as const;

function get<T>(col: Column<T>, row: T): unknown {
  return col.value ? col.value(row) : (row as any)?.[col.key];
}

function cmp(a: unknown, b: unknown): number {
  const na = a === null || a === undefined || (typeof a === "number" && !Number.isFinite(a));
  const nb = b === null || b === undefined || (typeof b === "number" && !Number.isFinite(b));
  if (na && nb) return 0;
  if (na) return 1; // missing always last
  if (nb) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

/** Heat bucket for a cell: background token and, for the dark end, paper text. */
function heatStyle(v: number, h: NonNullable<Column<unknown>["heat"]>): { background: string; color?: string } | undefined {
  if (!Number.isFinite(v)) return undefined;
  const diverging = h.diverging ?? (h.min === undefined || h.min < 0);
  if (diverging) {
    const m = Math.max(Math.abs(h.min ?? -1), Math.abs(h.max ?? 1)) || 1;
    const t = Math.max(-1, Math.min(1, v / m));
    const a = Math.abs(t);
    if (a < 0.05) return undefined;
    const k = a < 0.34 ? 1 : a < 0.67 ? 2 : 3;
    return { background: `var(--div-${t < 0 ? "neg" : "pos"}-${k})`, color: k === 3 ? "var(--paper)" : undefined };
  }
  const lo = h.min ?? 0;
  const hi = h.max ?? 1;
  const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo || 1)));
  const k = Math.min(5, 1 + Math.floor(t * 5));
  return { background: `var(--seq-${k})`, color: k >= 4 ? "var(--paper)" : undefined };
}

export function DataTable<T>({ columns, rows, rowKey, onRowClick, defaultSort, compact = true, maxHeight, empty, caption, className, footer, isActive }: DataTableProps<T>) {
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | undefined>(defaultSort);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return rows;
    const out = [...rows].sort((a, b) => cmp(get(col, a), get(col, b)));
    if (sort.dir === "desc") {
      // keep missing values last when descending
      const present = out.filter((r) => { const v = get(col, r); return v !== null && v !== undefined && !(typeof v === "number" && !Number.isFinite(v)); });
      const missing = out.filter((r) => !present.includes(r));
      return [...present.reverse(), ...missing];
    }
    return out;
  }, [rows, columns, sort]);

  const [cursor, setCursor] = useState(0);
  const body = useRef<HTMLTableSectionElement>(null);
  const focusRow = (i: number) => {
    const n = sorted.length;
    if (!n) return;
    const k = Math.max(0, Math.min(n - 1, i));
    setCursor(k);
    (body.current?.children[k] as HTMLElement | undefined)?.focus();
  };
  const onRowKey = (e: KeyboardEvent<HTMLTableRowElement>, row: T, i: number) => {
    const to = e.key === "ArrowDown" ? i + 1 : e.key === "ArrowUp" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? sorted.length - 1 : null;
    if (to !== null) {
      e.preventDefault();
      focusRow(to);
    } else if ((e.key === "Enter" || e.key === " ") && onRowClick) {
      e.preventDefault();
      onRowClick(row);
    }
  };
  const activeRow = Math.min(cursor, Math.max(0, sorted.length - 1));

  const onSort = (c: Column<T>) => {
    if (c.sortable === false) return;
    setSort((s) => (s?.key === c.key ? { key: c.key, dir: s.dir === "asc" ? "desc" : "asc" } : { key: c.key, dir: c.numeric ? "desc" : "asc" }));
  };

  const hideCls = (c: Column<T>) => {
    if (!c.hideBelow) return "";
    const bp = HIDE_BREAKPOINTS.find((b) => c.hideBelow! <= b) ?? HIDE_BREAKPOINTS[HIDE_BREAKPOINTS.length - 1];
    return `oc-hide-below-${bp}`;
  };

  return (
    <div className={`oc-table-wrap ${className ?? ""}`} style={{ maxHeight }}>
      <table className={`oc-table ${compact ? "oc-table-compact" : ""} ${onRowClick ? "oc-table-clickable" : ""}`}>
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              const align = c.align ?? (c.numeric ? "right" : "left");
              return (
                <th
                  key={c.key}
                  style={{ width: c.width, textAlign: align }}
                  className={`${c.sortable === false ? "" : "sortable"} ${active ? "sorted" : ""} ${hideCls(c)}`}
                  aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}
                  title={c.title}
                >
                  <span className="oc-th-inner" style={{ justifyContent: align === "right" ? "flex-end" : align === "center" ? "center" : "flex-start" }}>
                    <button type="button" className="oc-th-btn" onClick={() => onSort(c)} disabled={c.sortable === false}>
                      {c.label}
                      {active && <span className="oc-th-sort" aria-hidden="true">{sort!.dir === "asc" ? "↑" : "↓"}</span>}
                    </button>
                    {c.info && <InfoTip info={c.info} size={12} label={typeof c.label === "string" ? c.label : undefined} />}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody ref={body}>
          {sorted.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="oc-table-empty">
                {empty ?? "No rows"}
              </td>
            </tr>
          )}
          {sorted.map((row, i) => (
            <tr
              key={rowKey ? rowKey(row, i) : i}
              onClick={onRowClick ? () => { setCursor(i); onRowClick(row); } : undefined}
              onKeyDown={onRowClick ? (e) => onRowKey(e, row, i) : undefined}
              onFocus={onRowClick ? () => setCursor(i) : undefined}
              tabIndex={onRowClick ? (i === activeRow ? 0 : -1) : undefined}
              className={isActive?.(row) ? "active" : undefined}
            >
              {columns.map((c) => {
                const v = get(c, row);
                const align = c.align ?? (c.numeric ? "right" : "left");
                let colorCls = "";
                let colorStyle: string | undefined;
                if (c.color === "sign" || c.color === "sign-inverted") {
                  if (typeof v === "number" && v !== 0) colorCls = (v > 0) !== (c.color === "sign-inverted") ? "gain" : "loss";
                } else if (typeof c.color === "function") {
                  const r = c.color(v, row);
                  if (r && /^(gain|loss|warn|muted|subtle)$/.test(r)) colorCls = r;
                  else colorStyle = r;
                }
                const heat = c.heat && typeof v === "number" ? heatStyle(v, c.heat as any) : undefined;
                const content = c.render ? c.render(row) : c.format ? c.format(v, row) : v === null || v === undefined || v === "" ? EM_DASH : String(v);
                return (
                  <td key={c.key} className={`${c.numeric ? "num" : ""} ${colorCls} ${hideCls(c)} ${c.wrap ? "oc-td-wrap" : ""} ${c.className ?? ""}`} style={{ textAlign: align, color: heat?.color ?? colorStyle, background: heat?.background }}>
                    {content}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
        {footer && <tfoot>{footer}</tfoot>}
      </table>
    </div>
  );
}
