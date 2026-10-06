/**
 * Empty / error / loading / note states, set as labels: NO DATA, DATA UNAVAILABLE,
 * API UNREACHABLE, ERROR. Panel uses these automatically; use them directly when you are
 * not inside a Panel. The server's detail is shown verbatim in mono; nothing is invented.
 */
import type { ReactNode } from "react";
import { ApiError, DataUnavailableError, NetworkError } from "../lib/api";
import type { IconName } from "./Icon";

/** NO DATA, with the reason (`title`). `icon` is accepted for compatibility and not rendered. */
export function EmptyState({ title, children, action, compact }: { icon?: IconName; title: ReactNode; children?: ReactNode; action?: ReactNode; compact?: boolean }) {
  return (
    <div className={`oc-state oc-state-empty ${compact ? "oc-state-compact" : ""}`}>
      <div className="oc-state-title">NO DATA</div>
      <div className="oc-state-reason">{title}</div>
      {children && <div className="oc-state-text">{children}</div>}
      {action && <div className="oc-state-action">{action}</div>}
    </div>
  );
}

/**
 * Renders any thrown error. DataUnavailableError (HTTP 503) reads DATA UNAVAILABLE with
 * the server's reason verbatim.
 */
export function ErrorState({ error, onRetry, compact }: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  const retry = onRetry && (
    <button className="btn btn-sm" onClick={onRetry}>
      Retry
    </button>
  );
  let title: string;
  let detail: string;
  let cls = "oc-state-error";
  let role: "status" | "alert" = "alert";
  if (error instanceof DataUnavailableError) {
    title = "DATA UNAVAILABLE";
    detail = error.detail;
    cls = "oc-state-unavailable";
    role = "status";
  } else if (error instanceof NetworkError) {
    title = "API UNREACHABLE";
    detail = error.detail;
  } else {
    const status = error instanceof ApiError ? error.status : undefined;
    detail = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : String(error);
    const what = status === 422 ? "INVALID INPUT" : status === 404 ? "NOT FOUND" : "ERROR";
    title = status ? `${what} · HTTP ${status}` : what;
  }
  return (
    <div className={`oc-state ${cls} ${compact ? "oc-state-compact" : ""}`} role={role}>
      <div className="oc-state-title">{title}</div>
      {detail && <div className="oc-state-detail num">{detail}</div>}
      {retry && <div className="oc-state-action">{retry}</div>}
    </div>
  );
}

/** Flat placeholder block (no shimmer). */
export function Skeleton({ height = 16, width = "100%", style }: { height?: number | string; width?: number | string; style?: React.CSSProperties }) {
  return <div className="skeleton" style={{ height, width, ...style }} />;
}

/** A chart-shaped skeleton (used by Panel while loading). */
export function ChartSkeleton({ height = 280 }: { height?: number }) {
  return (
    <div className="oc-chart-skeleton" style={{ height }}>
      <Skeleton height="100%" />
    </div>
  );
}

/** Footnote-style note for model caveats. `warn` sets the marker in --signal. */
export function Callout({ tone = "note", title, children }: { tone?: "note" | "warn" | "info"; title?: ReactNode; children: ReactNode }) {
  return (
    <div className={`oc-callout oc-callout-${tone}`}>
      {title && <div className="oc-callout-title">{title}</div>}
      <div>{children}</div>
    </div>
  );
}
