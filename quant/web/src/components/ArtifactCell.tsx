/**
 * A Cell backed by one job kind's latest artifact (GET /api/artifacts/{kind}/latest).
 *  - held for pre-registration → AWAITING PRE-REGISTRATION, and nothing is read;
 *  - 404 → INSUFFICIENT DATA · NOT YET RUN; 503 → INSUFFICIENT DATA · DATA UNAVAILABLE;
 *  - present → the verdict first (when the product has one), then the body, with the
 *    artifact's data_asof in the header and the stale mark past a missed night.
 */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Panel } from "./Panel";
import { Absent, Verdict } from "../design";
import { NIGHTLY_MAX_AGE_SEC, PREREGISTERED, absentLabel, manifestOf, useArtifact, type Manifest } from "../lib/artifacts";
import { fmtAuto } from "../lib/format";

export interface ArtifactCellProps {
  title: string;
  kind: string;
  /** The page this cell links to, and its label (omit on that page itself). */
  to?: string;
  go?: string;
  /** The pre-registration this product waits on (e.g. "EXP-Q01"). */
  experiment?: string;
  span?: 2 | "all";
  /** Controls in the header (beside the link), e.g. a confidence toggle. */
  controls?: ReactNode;
  children?: (m: Manifest) => ReactNode;
}

export function ArtifactCell({ title, kind, to, go, experiment, span, controls, children }: ArtifactCellProps) {
  const held = experiment !== undefined && !PREREGISTERED[kind];
  const q = useArtifact(kind, !held);
  const link = to ? (
    <Link to={to} className="oc-go">
      {go ?? "OPEN"} →
    </Link>
  ) : null;
  const actions = link || controls ? (
    <>
      {controls}
      {link}
    </>
  ) : undefined;
  if (held)
    return (
      <Panel title={title} actions={link ?? undefined} span={span}>
        <Absent value="AWAITING PRE-REGISTRATION" reason={experiment ?? ""} source={kind} />
      </Panel>
    );
  const absent = q.isError ? absentLabel(q.error) : null;
  const m = manifestOf(q.data);
  if (absent || (q.data !== undefined && !m))
    return (
      <Panel title={title} actions={link ?? undefined} span={span}>
        <Absent reason={absent ?? "NO MANIFEST"} source={kind} />
      </Panel>
    );
  if (!m) return <Panel title={title} actions={link ?? undefined} span={span} loading={q.isLoading} error={q.isError ? q.error : undefined} onRetry={() => void q.refetch()} skeletonHeight={120} />;
  return (
    <Panel
      title={title}
      actions={actions}
      span={span}
      asOf={m.data_asof ?? m.finished_at ?? undefined}
      // Lane B's {stale: true} wins over the clock: a negative max age is always exceeded.
      maxAgeSec={m.stale ? -1 : NIGHTLY_MAX_AGE_SEC}
      provenance={m.provenance}
      notes={m.notes}
    >
      <div className="oc-art">
        {m.verdict && <Verdict value={m.verdict} />}
        {children ? children(m) : <Headline m={m} />}
        <div className="oc-art-id num">
          {kind} · {m.id}
          {m.engine ? ` · ${m.engine.toUpperCase()}` : ""}
        </div>
      </div>
    </Panel>
  );
}

/** A product's headline readings as a ruled label / value list. */
export function Headline({ m, omit = [] }: { m: Manifest; omit?: string[] }) {
  const rows = Object.entries(m.headline ?? {}).filter(([k]) => !omit.includes(k));
  if (!rows.length) return null;
  return (
    <dl className="oc-kv">
      {rows.map(([k, v]) => (
        <div key={k} className="oc-kv-row">
          <dt>{k.replace(/_/g, " ")}</dt>
          <dd className="num">{typeof v === "number" ? fmtAuto(v) : (v ?? "—")}</dd>
        </div>
      ))}
    </dl>
  );
}
