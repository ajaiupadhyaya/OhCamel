/**
 * Sortable, compact data table with numeric alignment, conditional colouring and a
 * sticky header (set `maxHeight` to scroll inside the table).
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
 * `color: "sign"` colours gains/losses; a function may return any CSS colour or class.
 * `heat` shades the cell background on the diverging (signed) or sequential ramp.
 * `wrap` lets a long-text column (e.g. references) wrap; give it a `width` to bound it.
 * `hideBelow` hides a column under a viewport width (600 / 900 / 1200 / 1440 / 1600).
 */
import { useMemo, useState, type ReactNode } from "react";
import { EM_DASH } from "../lib/format";
import { InfoTip, type InfoProp } from "./InfoTip";
import { Icon } from "./Icon";

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

function heatBg(v: number, h: NonNullable<Column<unknown>["heat"]>): string | undefined {
  if (!Number.isFinite(v)) return undefined;
  const diverging = h.diverging ?? (h.min === undefined || h.min < 0);
  if (diverging) {
    const m = Math.max(Math.abs(h.min ?? -1), Math.abs(h.max ?? 1)) || 1;
    const t = Math.max(-1, Math.min(1, v / m));
    const pct = Math.round(Math.abs(t) * 38);
    return `color-mix(in srgb, var(${t >= 0 ? "--gain" : "--loss"}) ${pct}%, transparent)`;
  }
  const lo = h.min ?? 0;
  const hi = h.max ?? 1;
  const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo || 1)));
  return `color-mix(in srgb, var(--accent) ${Math.round(t * 34)}%, transparent)`;
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
                      {c.sortable !== false && <Icon name={active ? (sort!.dir === "asc" ? "chevron-up" : "chevron-down") : "sort"} size={11} className="oc-th-sort" />}
                    </button>
                    {c.info && <InfoTip info={c.info} size={12} label={typeof c.label === "string" ? c.label : undefined} />}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
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
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              onKeyDown={onRowClick ? (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onRowClick(row)) : undefined}
              tabIndex={onRowClick ? 0 : undefined}
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
                const bg = c.heat && typeof v === "number" ? heatBg(v, c.heat as any) : undefined;
                const content = c.render ? c.render(row) : c.format ? c.format(v, row) : v === null || v === undefined || v === "" ? EM_DASH : String(v);
                return (
                  <td key={c.key} className={`${c.numeric ? "num" : ""} ${colorCls} ${hideCls(c)} ${c.wrap ? "oc-td-wrap" : ""} ${c.className ?? ""}`} style={{ textAlign: align, color: colorStyle, background: bg }}>
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
