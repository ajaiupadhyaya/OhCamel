/**
 * The data panel: title (+ info), actions, body, provenance footer, notes callout, and
 * built-in loading / error / empty states.
 *
 * Simplest use — hand it a react-query result and a render function. Provenance and
 * notes are picked up from the payload automatically (`data.provenance`, `data.notes`):
 *
 *   const q = useApiQuery<Overview>("/market/overview", { universe });
 *   <Panel title="Sectors" info="…" query={q} skeletonHeight={320}>
 *     {(data) => <Heatmap … />}
 *   </Panel>
 *
 * Or fully manual: <Panel title="…" loading={…} error={…} provenance={…} notes={…}>…</Panel>
 * `flush` removes body padding (for edge-to-edge tables).
 */
import { useState, type ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import type { ProvenanceRecord } from "../lib/types";
import { InfoTip, type InfoProp } from "./InfoTip";
import { Provenance } from "./Provenance";
import { Callout, ChartSkeleton, EmptyState, ErrorState } from "./States";

type Renderable<T> = ReactNode | ((data: T) => ReactNode);

export interface PanelProps<T> {
  title?: ReactNode;
  subtitle?: ReactNode;
  info?: InfoProp;
  actions?: ReactNode;
  query?: Pick<UseQueryResult<T, unknown>, "data" | "error" | "isLoading" | "isFetching" | "refetch" | "isError">;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  /**
   * Empty state. Takes precedence over `error` and loading: a panel that has nothing to show
   * for this input should not surface an unrelated error from its query.
   */
  empty?: boolean | ReactNode;
  /**
   * `false` = this panel does not apply to the current input (e.g. an options-only panel for
   * a ticker with no chain). Renders the `empty` content (or a default "Not applicable")
   * and suppresses error / loading / notes / provenance.
   */
  applicable?: boolean;
  provenance?: ProvenanceRecord[] | null;
  notes?: string[] | string | null;
  footer?: ReactNode;
  skeletonHeight?: number;
  flush?: boolean;
  /** Use the compact error/empty treatment (for small panels in long lists). */
  compact?: boolean;
  className?: string;
  /** grid span helper: 2 = .span-2, "all" = full row */
  span?: 2 | "all";
  children?: Renderable<T>;
  id?: string;
}

function asList(n: string[] | string | null | undefined): string[] {
  if (!n) return [];
  return (Array.isArray(n) ? n : [n]).filter((x) => typeof x === "string" && x.trim());
}

export function Panel<T = unknown>(props: PanelProps<T>) {
  const { title, subtitle, info, actions, query, footer, skeletonHeight = 260, flush, compact, className, span, children, id } = props;
  const data = query?.data;
  const loading = props.loading ?? (query ? query.isLoading : false);
  const error = props.error ?? (query?.isError ? query.error : undefined);
  const onRetry = props.onRetry ?? (query ? () => void query.refetch() : undefined);
  const payload = (data ?? {}) as { provenance?: ProvenanceRecord[]; notes?: string[] | string };
  const provenance = props.provenance ?? (data && typeof data === "object" ? payload.provenance : undefined);
  const notes = asList(props.notes ?? (data && typeof data === "object" ? payload.notes : undefined));
  const refreshing = !!query?.isFetching && !loading && props.applicable !== false;

  const notApplicable = props.applicable === false;
  const emptyNode = (fallback: string) => (props.empty && props.empty !== true ? props.empty : <EmptyState title={fallback} compact={compact} />);
  let body: ReactNode;
  if (notApplicable) body = emptyNode("Not applicable here");
  else if (props.empty) body = emptyNode("Nothing to show yet");
  else if (error) body = <ErrorState error={error} onRetry={onRetry} compact={compact} />;
  else if (loading) body = <ChartSkeleton height={skeletonHeight} />;
  else if (typeof children === "function") body = query ? (data !== undefined ? (children as (d: T) => ReactNode)(data as T) : null) : null;
  else body = children;

  const spanCls = span === 2 ? "span-2" : span === "all" ? "span-all" : "";
  return (
    <section id={id} className={`oc-panel ${spanCls} ${className ?? ""}`} aria-busy={loading || refreshing}>
      {(title || actions) && (
        <header className="oc-panel-head">
          <div className="oc-panel-titles">
            {title && (
              <h3 className="oc-panel-title">
                {title}
                <InfoTip info={info} label={typeof title === "string" ? title : undefined} />
              </h3>
            )}
            {subtitle && <div className="oc-panel-subtitle">{subtitle}</div>}
          </div>
          {actions && <div className="oc-panel-actions">{actions}</div>}
        </header>
      )}
      {refreshing && <div className="oc-panel-progress" />}
      <div className={`oc-panel-body ${flush && !error && !notApplicable ? "oc-panel-body-flush" : ""}`}>{body}</div>
      {!error && !loading && !notApplicable && notes.length > 0 && (
        <div className="oc-panel-notes">
          <Notes notes={notes} />
        </div>
      )}
      {(footer || (provenance && provenance.length > 0)) && !error && !notApplicable && (
        <footer className="oc-panel-foot">
          {footer}
          <Provenance items={provenance} />
        </footer>
      )}
    </section>
  );
}

/** Model/data notes: the first two are shown; the rest behind "N more". */
export function Notes({ notes, initial = 2 }: { notes: string[]; initial?: number }) {
  const [all, setAll] = useState(false);
  const shown = all ? notes : notes.slice(0, initial);
  const more = notes.length - shown.length;
  return (
    <Callout>
      {shown.length === 1 && !more ? shown[0] : <ul>{shown.map((n, i) => <li key={i}>{n}</li>)}</ul>}
      {(more > 0 || all) && notes.length > initial && (
        <button type="button" className="oc-notes-more" onClick={() => setAll((a) => !a)}>
          {all ? "Show fewer" : `${more} more note${more > 1 ? "s" : ""}`}
        </button>
      )}
    </Callout>
  );
}

/** Alias: some authors think of it as a Card. */
export const Card = Panel;
