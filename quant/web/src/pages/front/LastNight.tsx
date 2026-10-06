/** LAST NIGHT: the job counts and core-hours of the last 24h from GET /api/ops. */
import { Link } from "react-router-dom";
import { ReadCell, StatGrid, StatTile } from "../../components";
import { Absent } from "../../design";
import { fmtNum } from "../../lib/format";
import { useOps, type OpsOut } from "../../shell/ops";

export function LastNight() {
  const ops = useOps();
  return (
    <ReadCell<OpsOut>
      title="LAST NIGHT · 24H"
      query={ops}
      source="GET /api/ops"
      notes={[]}
      skeletonHeight={96}
      actions={
        <Link to="/compute" className="oc-go">
          COMPUTE →
        </Link>
      }
    >
      {(d) => {
        const j = d.jobs;
        if (!j) return <Absent reason="NOT YET RUN" source="GET /api/ops · jobs" />;
        const failed = j.failed_24h ?? null;
        return (
          <StatGrid min={110}>
            <StatTile size="sm" label="DONE" value={j.done_24h != null ? fmtNum(j.done_24h, 0) : null} />
            <StatTile size="sm" label="FAILED" value={failed != null ? fmtNum(failed, 0) : null} tone={failed ? "loss" : "neutral"} />
            <StatTile size="sm" label="CORE-H" value={j.cpu_seconds_24h != null ? fmtNum(j.cpu_seconds_24h / 3600, 1) : null} />
            <StatTile size="sm" label="QUEUED · RUN" value={`${j.queued ?? "—"} · ${j.running ?? "—"}`} />
          </StatGrid>
        );
      }}
    </ReadCell>
  );
}
