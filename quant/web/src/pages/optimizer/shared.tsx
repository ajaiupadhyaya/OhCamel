/**
 * Page-local plumbing: the optimizer context (config + setters), a Cell wrapper that turns
 * the "risk-free rate unavailable" 503 into INSUFFICIENT DATA with a one-click switch to a
 * user-supplied rate, and small helpers. Paper Tape: rules, caps labels, mono numbers.
 */
import { createContext, useContext, type CSSProperties, type ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Panel, type PanelProps } from "../../components";
import { Absent } from "../../design";
import { DataUnavailableError, type ApiError } from "../../lib/api";
import type { OptConfig } from "./config";
import type { MethodsCatalog, MethodSpec } from "./types";

export interface OptCtx {
  cfg: OptConfig;
  set: (p: Partial<OptConfig>) => void;
  catalog?: MethodsCatalog;
  spec?: MethodSpec;
  /** true once any request reported the market risk-free series as unavailable */
  rfMissing: boolean;
}

const Ctx = createContext<OptCtx | null>(null);
export const OptProvider = Ctx.Provider;
export function useOpt(): OptCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useOpt outside the Optimizer page");
  return v;
}

export function isRfError(e: unknown): boolean {
  return e instanceof DataUnavailableError && /risk[-_ ]free/i.test(e.detail);
}

/**
 * Cell for an optimizer query. A 503 caused by the risk-free series reads INSUFFICIENT DATA ·
 * RISK-FREE UNAVAILABLE (nothing is computed on an assumed rate) with SET RF beside it.
 */
export function QPanel<T>({
  q,
  children,
  ...props
}: Omit<PanelProps<T>, "query" | "children"> & {
  q: UseQueryResult<T, ApiError>;
  children: (d: T) => ReactNode;
}) {
  const { set, cfg } = useOpt();
  if (isRfError(q.error)) {
    return (
      <Panel<T> {...props} notes={undefined} actions={undefined}>
        <div className="op-rf-missing">
          <Absent reason="RISK-FREE UNAVAILABLE" source="FRED DGS3MO · K. FRENCH RF" />
          <button type="button" className="btn btn-sm" onClick={() => set({ rfMode: "manual" })}>
            {cfg.rfMode === "manual" ? "EDIT RF" : "SET RF"}
          </button>
        </div>
      </Panel>
    );
  }
  return (
    <Panel<T> {...props} query={q}>
      {children}
    </Panel>
  );
}

/** Tickers sorted by a numeric map, descending. */
export function sortBy(tickers: string[], m: Record<string, number | null | undefined>): string[] {
  return [...tickers].sort((a, b) => (m[b] ?? -Infinity) - (m[a] ?? -Infinity));
}

/** A number with a hairline bar under it, scaled to `max` (weights, risk shares). Shorts draw ink-3. */
export function BarCell({ v, max, text, dim }: { v: number | null | undefined; max: number; text: string; dim?: boolean }) {
  const w = v == null || !Number.isFinite(v) ? 0 : Math.min(100, (Math.abs(v) / (max || 1)) * 100);
  return (
    <span className={`op-barcell ${dim ? "op-barcell-dim" : ""} ${v != null && v < 0 ? "op-barcell-neg" : ""}`}>
      <span className="op-barcell-v">{text}</span>
      <span className="op-barcell-bar" aria-hidden style={{ "--w": `${w}%` } as CSSProperties} />
    </span>
  );
}

type ReadItem = { k: string; v: ReactNode; tone?: string };

/** A terse ruled readout line: KEY value · KEY value (labels, not sentences). */
export function Readline({ items }: { items: (ReadItem | false | null | undefined)[] }) {
  return (
    <div className="op-readline num">
      {items
        .filter((it): it is ReadItem => !!it)
        .map((it, i) => (
          <span key={i} className="op-readline-item">
            <span className="op-readline-k">{it.k}</span> <span className={it.tone ?? ""}>{it.v}</span>
          </span>
        ))}
    </div>
  );
}

/**
 * A label with its Greek letters kept lowercase inside an uppercased caps label (μ would set
 * as Μ, which reads as M; ρ as P; τ as T).
 */
export function sym(s: string): ReactNode {
  const parts = s.split(/([α-ω]+)/);
  if (parts.length === 1) return s;
  return parts.map((p, i) =>
    /[α-ω]/.test(p) ? (
      <span key={i} className="op-sym">
        {p}
      </span>
    ) : (
      p
    ),
  );
}
