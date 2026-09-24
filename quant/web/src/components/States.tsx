/**
 * Empty / error / loading / note states. Panel uses these automatically; use them
 * directly when you are not inside a Panel.
 */
import type { ReactNode } from "react";
import { ApiError, DataUnavailableError, NetworkError } from "../lib/api";
import { Icon, type IconName } from "./Icon";

export function EmptyState({ icon = "grid", title, children, action, compact }: { icon?: IconName; title: ReactNode; children?: ReactNode; action?: ReactNode; compact?: boolean }) {
  return (
    <div className={`oc-state ${compact ? "oc-state-compact" : ""}`}>
      <div className="oc-state-icon">
        <Icon name={icon} size={20} />
      </div>
      <div className="oc-state-title">{title}</div>
      {children && <div className="oc-state-text">{children}</div>}
      {action && <div className="oc-state-action">{action}</div>}
    </div>
  );
}

/**
 * Renders any thrown error. DataUnavailableError (HTTP 503) gets the lilac
 * "data source unavailable" treatment with the server's reason verbatim.
 */
export function ErrorState({ error, onRetry, compact }: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  const retry = onRetry && (
    <button className="btn btn-sm" onClick={onRetry}>
      <Icon name="refresh" size={14} /> Try again
    </button>
  );
  if (error instanceof DataUnavailableError) {
    return (
      <div className={`oc-state oc-state-unavailable ${compact ? "oc-state-compact" : ""}`} role="status">
        <div className="oc-state-icon">
          <Icon name="cloud-off" size={20} />
        </div>
        <div className="oc-state-title">Data source unavailable</div>
        <div className="oc-state-detail num">{error.detail}</div>
        <div className="oc-state-text">Nothing is shown rather than an approximation. This clears when the source is reachable again.</div>
        {retry && <div className="oc-state-action">{retry}</div>}
      </div>
    );
  }
  if (error instanceof NetworkError) {
    return (
      <div className={`oc-state oc-state-error ${compact ? "oc-state-compact" : ""}`} role="alert">
        <div className="oc-state-icon">
          <Icon name="alert" size={20} />
        </div>
        <div className="oc-state-title">Can't reach the API</div>
        <div className="oc-state-text">{error.detail}</div>
        {retry && <div className="oc-state-action">{retry}</div>}
      </div>
    );
  }
  const status = error instanceof ApiError ? error.status : undefined;
  const detail = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : String(error);
  const title = status === 422 ? "Those inputs can't be used" : status === 404 ? "Not found" : "Something went wrong";
  return (
    <div className={`oc-state oc-state-error ${compact ? "oc-state-compact" : ""}`} role="alert">
      <div className="oc-state-icon">
        <Icon name="alert" size={20} />
      </div>
      <div className="oc-state-title">
        {title}
        {status ? <span className="badge loss" style={{ marginLeft: 8 }}>HTTP {status}</span> : null}
      </div>
      <div className="oc-state-detail num">{detail}</div>
      {retry && <div className="oc-state-action">{retry}</div>}
    </div>
  );
}

/** Shimmering placeholder block. */
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

/** Subtle callout for model notes / caveats (Panel renders `notes` with this). */
export function Callout({ tone = "note", title, children }: { tone?: "note" | "warn" | "info"; title?: ReactNode; children: ReactNode }) {
  return (
    <div className={`oc-callout oc-callout-${tone}`}>
      <Icon name={tone === "warn" ? "alert" : "info"} size={15} />
      <div>
        {title && <div className="oc-callout-title">{title}</div>}
        <div>{children}</div>
      </div>
    </div>
  );
}
