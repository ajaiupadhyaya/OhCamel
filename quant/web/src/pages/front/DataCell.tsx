/** DATA: freshness per warehouse dataset (GET /api/warehouse/freshness), one lamp each. */
import { Link } from "react-router-dom";
import { DataTable, ReadCell, type Column } from "../../components";
import { Lamp } from "../../design";
import { fmtStamp } from "../../design/stamp";
import { fmtNum } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import { freshnessRows, lampOf, type FreshnessRow } from "./data";

export function DataCell() {
  const q = useApiQuery<unknown>("/warehouse/freshness", undefined, { refetchInterval: 5 * 60_000 });
  const now = new Date();
  const cols: Column<FreshnessRow>[] = [
    {
      key: "dataset",
      label: "DATASET",
      render: (r) => (
        <span className="fp-ds num">
          <Lamp state={lampOf(r, now)} label={r.dataset} />
          {r.dataset}
        </span>
      ),
    },
    { key: "keys", label: "KEYS", numeric: true, hideBelow: 600, format: (v) => fmtNum(v, 0) },
    { key: "keys_behind", label: "BEHIND", numeric: true, hideBelow: 600, format: (v) => fmtNum(v, 0) },
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
