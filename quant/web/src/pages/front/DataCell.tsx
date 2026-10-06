/** DATA: freshness per warehouse dataset (GET /api/warehouse/freshness), one lamp each. */
import { Link } from "react-router-dom";
import { DataTable, ReadCell, type Column } from "../../components";
import { Lamp, isStale, type LampState } from "../../design";
import { fmtStamp } from "../../design/stamp";
import { NIGHTLY_MAX_AGE_SEC } from "../../lib/artifacts";
import { useApiQuery } from "../../lib/query";
import { freshnessRows, type FreshnessRow } from "./data";

function lampOf(r: FreshnessRow, now: Date): LampState {
  if (r.status && /fail|error/i.test(r.status)) return "fault";
  return isStale(r.data_asof, r.max_age_s ?? NIGHTLY_MAX_AGE_SEC, now) ? "stale" : "ok";
}

export function DataCell() {
  const q = useApiQuery<unknown>("/warehouse/freshness", undefined, { refetchInterval: 5 * 60_000 });
  const now = new Date();
  const cols: Column<FreshnessRow>[] = [
    { key: "lamp", label: "", width: 24, render: (r) => <Lamp state={lampOf(r, now)} label={r.dataset} /> },
    { key: "dataset", label: "DATASET", render: (r) => <span className="num">{r.dataset}</span> },
    { key: "data_asof", label: "DATA AS OF", align: "right", render: (r) => <span className="num">{fmtStamp(r.data_asof)}</span> },
    { key: "state", label: "STATE", align: "right", value: (r) => lampOf(r, now), render: (r) => <span className={lampOf(r, now) === "ok" ? "num" : "num loss"}>{lampOf(r, now).toUpperCase()}</span> },
  ];
  return (
    <ReadCell<unknown>
      title="DATA · FRESHNESS"
      query={q}
      source="GET /api/warehouse/freshness"
      flush
      skeletonHeight={96}
      actions={
        <Link to="/compute" className="oc-go">
          COMPUTE →
        </Link>
      }
    >
      {(d) => <DataTable<FreshnessRow> columns={cols} rows={freshnessRows(d)} rowKey={(r) => r.dataset} empty="NO DATASETS" />}
    </ReadCell>
  );
}
