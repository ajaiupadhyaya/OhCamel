/**
 * Building blocks shared by the Research views: ticker availability, the verdict block (the
 * stamp, its figures, the charter gates), a ruled readout line and number helpers. Paper
 * Tape: rules, caps labels, mono numbers.
 */
import { useMemo, type CSSProperties, type ReactNode } from "react";
import { DataTable, type Column } from "../../components";
import { Verdict, type VerdictValue } from "../../design";
import { fmtNum, fmtPct } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import type { GateStatus, LabGate } from "./derive";

// ------------------------------------------------------------------ availability

interface OverviewLite {
  as_of?: string | null;
  rows: { ticker: string; error?: string | null; as_of?: string | null }[];
}

export type Availability = {
  status: (t: string) => "ok" | "missing" | "pending";
  reason: (t: string) => string | undefined;
  loading: boolean;
  asOf: string | null;
};

/**
 * Which tickers any configured data source can serve, via GET /market/overview (cheap,
 * cached). Offline, only the committed fixtures are available. If the check itself fails,
 * tickers are treated as available and the backtest reports any real problem.
 */
export function useAvailability(tickers: string[]): Availability {
  const list = useMemo(() => [...new Set(tickers.map((t) => t.toUpperCase()))].sort(), [tickers]);
  const q = useApiQuery<OverviewLite>("/market/overview", { tickers: list.join(",") }, { enabled: list.length > 0, staleTime: 30 * 60_000 });
  return useMemo(() => {
    const rows = new Map((q.data?.rows ?? []).map((r) => [r.ticker.toUpperCase(), r]));
    const failed = q.isError;
    return {
      status: (t: string) => {
        if (failed) return "ok";
        const r = rows.get(t.toUpperCase());
        if (!r) return "pending";
        return r.error ? "missing" : "ok";
      },
      reason: (t: string) => rows.get(t.toUpperCase())?.error ?? undefined,
      loading: q.isLoading || (q.isFetching && list.some((t) => !rows.has(t))),
      asOf:
        q.data?.as_of ??
        (q.data?.rows
          .map((r) => r.as_of)
          .filter(Boolean)
          .sort()
          .pop() as string | undefined) ??
        null,
    };
  }, [q.data, q.isError, q.isLoading, q.isFetching, list]);
}

// ------------------------------------------------------------------ verdict block

export interface Fig {
  k: ReactNode;
  v: ReactNode;
  sub?: ReactNode;
  tone?: "loss" | "";
}

/** Big mono figures under the verdict: label, value, one-line footnote. */
export function Figs({ items }: { items: Fig[] }) {
  return (
    <div className="sl-figs num" style={{ "--n": items.length } as CSSProperties}>
      {items.map((f, i) => (
        <div key={i}>
          <span className="sl-fig-k">{f.k}</span>
          <span className={`sl-fig-v ${f.tone ?? ""}`}>{f.v}</span>
          {f.sub && <span className="sl-fig-sub">{f.sub}</span>}
        </div>
      ))}
    </div>
  );
}

const statusClass = (s: GateStatus) => (s === "FAIL" ? "sl-gate-fail" : s === "PASS" || s === "REPORTED" ? "" : "sl-gate-dim");

/** The charter gates as a dense ruled table: GATE · VALUE · RULE · STATUS (FAIL in signal). */
export function GateTable({ gates }: { gates: LabGate[] }) {
  const cols: Column<LabGate>[] = [
    { key: "code", label: "Gate", sortable: false, render: (g) => <span className="num">{g.code}</span> },
    { key: "value", label: "Value", numeric: true, sortable: false, render: (g) => <span className={g.status === "NOT RUN" ? "sl-gate-dim" : ""}>{g.value}</span> },
    { key: "rule", label: "Rule", numeric: true, sortable: false, hideBelow: 420, render: (g) => <span className="sl-gate-dim">{g.rule}</span> },
    { key: "status", label: "Status", numeric: true, sortable: false, render: (g) => <span className={statusClass(g.status)}>{g.status}</span> },
  ];
  return <DataTable<LabGate> columns={cols} rows={gates} rowKey={(g) => g.code} compact />;
}

/** Verdict first, then figures, then the gates. */
export function VerdictBlock({ value, detail, figs, gates, children }: { value: VerdictValue; detail: string; figs?: Fig[]; gates?: LabGate[]; children?: ReactNode }) {
  return (
    <div className="sl-verdict">
      <Verdict value={value} detail={detail} />
      {figs && figs.length > 0 && <Figs items={figs} />}
      {gates && gates.length > 0 && <GateTable gates={gates} />}
      {children}
    </div>
  );
}

type ReadItem = { k: string; v: ReactNode; tone?: string };

/** A terse ruled readout line: KEY value · KEY value. */
export function Readline({ items }: { items: (ReadItem | false | null | undefined)[] }) {
  return (
    <div className="sl-readline num">
      {items
        .filter((it): it is ReadItem => !!it)
        .map((it, i) => (
          <span key={i}>
            <span className="sl-readline-k">{it.k}</span> <span className={it.tone ?? ""}>{it.v}</span>
          </span>
        ))}
    </div>
  );
}

// ------------------------------------------------------------------ number helpers

export const finite = (x: number | null | undefined): x is number => typeof x === "number" && Number.isFinite(x);

/** Sharpe-style ratio, 2 dp. */
export const fmtSR = (x: number | null | undefined, d = 2) => fmtNum(x, d);

/** Probability as a percent with sensible precision near the ends. */
export function fmtProb(p: number | null | undefined): string {
  if (!finite(p)) return "—";
  if (p > 0.999) return ">99.9%";
  if (p < 0.001) return "<0.1%";
  return fmtPct(p, p > 0.99 || p < 0.01 ? 1 : 0);
}

export function fmtYears(y: number | null | undefined): string {
  if (y === Infinity) return "∞";
  if (!finite(y)) return "—";
  return `${fmtNum(y, y >= 100 ? 0 : 1)}Y`;
}

/** A grid value as a label: up to six decimals, trailing zeros dropped. */
export const paramLabel = (v: number) => String(Math.round(v * 1e6) / 1e6);

/** Parameter names as caps labels: "vol_target" → "VOL TARGET". */
export const capsName = (s: string) => s.replace(/_/g, " ").toUpperCase();

/** Params as a terse mono line: LOOKBACK 252 · VOL TARGET 0.4. */
export function paramLine(params: Record<string, unknown> | null | undefined): string {
  if (!params) return "";
  return Object.entries(params)
    .map(([k, v]) => {
      const n = capsName(k);
      if (typeof v === "number") return `${n} ${paramLabel(v)}`;
      if (v && typeof v === "object") {
        const e = Object.entries(v as Record<string, number>);
        return e.length ? e.map(([t, w]) => `${t} ${fmtPct(w, 1)}`).join(" ") : `${n} EQUAL`;
      }
      if (v === "") return `${n} CASH`;
      if (typeof v === "boolean") return `${n} ${v ? "ON" : "OFF"}`;
      return `${n} ${String(v)}`;
    })
    .join(" · ");
}
