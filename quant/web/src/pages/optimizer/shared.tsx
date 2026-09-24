/**
 * Page-local plumbing: the optimizer context (config + setters), a Panel wrapper that turns
 * the "risk-free rate unavailable" 503 into an actionable state, and small helpers.
 */
import { createContext, useContext, type ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { ErrorState, Panel, type PanelProps } from "../../components";
import { Icon } from "../../components/Icon";
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
 * Panel for an optimizer query. A 503 caused by the risk-free series gets the standard
 * "data source unavailable" treatment plus a one-click switch to a user-supplied rate.
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
      <Panel<T> {...props} notes={undefined}>
        <div className="op-rf-missing">
          <ErrorState error={q.error} onRetry={() => void q.refetch()} />
          <div className="op-rf-missing-fix">
            <p>
              This view needs a risk-free rate (Sharpe ratios, the tangency
              portfolio or Black–Litterman equilibrium). The 3-month T-bill
              series could not be loaded, so nothing is computed rather than
              assuming one. You can supply the rate yourself — it will be
              labelled as user-supplied everywhere it is used.
            </p>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => set({ rfMode: "manual" })}
            >
              <Icon name="plus" size={14} />{" "}
              {cfg.rfMode === "manual"
                ? "Edit my risk-free rate in the rail"
                : "Enter a risk-free rate myself"}
            </button>
          </div>
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
export function sortBy(
  tickers: string[],
  m: Record<string, number | null | undefined>,
): string[] {
  return [...tickers].sort((a, b) => (m[b] ?? -Infinity) - (m[a] ?? -Infinity));
}

/** Top-k tickers by |value| with the remainder folded into "Other" (≤ 8 series rule). */
export function foldTop(
  tickers: string[],
  score: (t: string) => number,
  k = 7,
): { keep: string[]; other: string[] } {
  if (tickers.length <= k + 1) return { keep: tickers, other: [] };
  const ranked = [...tickers].sort((a, b) => score(b) - score(a));
  const keepSet = new Set(ranked.slice(0, k));
  return {
    keep: tickers.filter((t) => keepSet.has(t)),
    other: tickers.filter((t) => !keepSet.has(t)),
  };
}

/** "SPY 42.1% · TLT 20.0% · …" for hover labels (largest |w| first). */
export function weightsLine(
  w: Record<string, number> | undefined,
  max = 6,
  sep = "<br>",
): string {
  if (!w) return "";
  const rows = Object.entries(w)
    .filter(([, v]) => Math.abs(v) > 0.0005)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const shown = rows
    .slice(0, max)
    .map(([k, v]) => `${k} ${(v * 100).toFixed(1)}%`);
  if (rows.length > max) shown.push(`+${rows.length - max} more`);
  return shown.join(sep);
}
