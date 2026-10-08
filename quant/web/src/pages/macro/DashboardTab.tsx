/**
 * DASH — GET /api/macro/dashboard: every configured FRED indicator, one dense ruled table per
 * theme (latest, 1M / 3M / 1Y change, 10-year percentile, 3-year line, as of). An indicator FRED
 * cannot serve stays in its table as UNAVAILABLE, never as a number. A row opens the explorer.
 */
import { useMemo } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { DataTable, Panel, Sparkline, StatGrid, StatTile, type Column } from "../../components";
import { Note } from "../../design";
import type { ApiError } from "../../lib/api";
import { fmtDate, fmtNum } from "../../lib/format";
import { CATEGORY_LABEL, INFO, TRANSFORM_LABEL } from "./info";
import { PctTick, fmtUnits, ordinal } from "./shared";
import type { Dashboard, DashboardRow } from "./types";

const FREQ: Record<string, string> = { D: "DAILY", W: "WEEKLY", M: "MONTHLY", Q: "QUARTERLY" };
const HEADLINE: [string, string][] = [
  ["DFF", "FED FUNDS"],
  ["DGS10", "UST 10Y"],
  ["T10Y3M", "10Y − 3M"],
  ["PCEPILFE", "CORE PCE"],
  ["UNRATE", "UNEMPLOYMENT"],
  ["VIXCLS", "VIX"],
];

const ok = (r: DashboardRow) => !r.error && r.latest != null;

export function DashboardTab({ q, onExplore }: { q: UseQueryResult<Dashboard, ApiError>; onExplore: (id: string, transform: string) => void }) {
  const d = q.data;
  const groups = useMemo(() => {
    if (!d) return [];
    const m = new Map<string, DashboardRow[]>();
    for (const c of d.categories) m.set(c, []);
    for (const r of d.series) m.get(r.category)?.push(r);
    return [...m.entries()].filter(([, rows]) => rows.length);
  }, [d]);

  return (
    <div className="stack">
      <Panel<Dashboard>
        title={
          <>
            HEADLINE · FRED
            <Note n={1} to="macro" />
          </>
        }
        query={q}
        skeletonHeight={110}
        asOf={d?.series
          .map((r) => r.date)
          .filter((x): x is string => !!x)
          .sort()
          .pop()}
      >
        {(dd) => <Headline d={dd} />}
      </Panel>
      {d && (
        <div className="grid-2">
          {groups.map(([cat, rows]) => (
            <Panel<Dashboard>
              key={cat}
              title={`${CATEGORY_LABEL[cat] ?? cat.toUpperCase()} · ${rows.filter(ok).length}/${rows.length}`}
              query={q}
              flush
              notes={[]}
              provenance={[]}
              asOf={rows
                .map((r) => r.date)
                .filter((x): x is string => !!x)
                .sort()
                .pop()}
            >
              {() => <Indicators rows={rows} onExplore={onExplore} />}
            </Panel>
          ))}
        </div>
      )}
    </div>
  );
}

function Headline({ d }: { d: Dashboard }) {
  const byId = new Map(d.series.filter(ok).map((r) => [r.id, r]));
  const sahm = d.highlights?.sahm_rule;
  const tiles = HEADLINE.map(([id, label]) => [byId.get(id), label] as const).filter((x): x is readonly [DashboardRow, string] => !!x[0]);
  if (!tiles.length && !sahm) return <div className="mc-none num">NO HEADLINE SERIES AVAILABLE</div>;
  return (
    <StatGrid min={140}>
      {tiles.map(([r, label]) => (
        <StatTile key={r.id} size="sm" label={label} value={fmtUnits(r.latest, r.units)} caption={`${fmtUnits(r.change?.["1M"], r.units, { signed: true, change: true })} 1M · ${fmtDate(r.date, "short").toUpperCase()}`} />
      ))}
      {sahm && (
        <StatTile size="sm" label="SAHM" info={INFO.sahm} value={`${fmtNum(sahm.value, 2)} PP`} tone={sahm.triggered ? "loss" : "neutral"} caption={<span className={sahm.triggered ? "loss" : ""}>{sahm.triggered ? "TRIGGERED" : "BELOW"} · {fmtNum(sahm.threshold, 2)} PP</span>} />
      )}
    </StatGrid>
  );
}

function Indicators({ rows, onExplore }: { rows: DashboardRow[]; onExplore: (id: string, transform: string) => void }) {
  const cols: Column<DashboardRow>[] = [
    { key: "id", label: "Series", render: (r) => <span className="num" title={r.name}>{r.id}</span> },
    {
      key: "name",
      label: "Name",
      hideBelow: 600,
      render: (r) => (
        <span className={ok(r) ? "" : "mc-dim"} title={r.error ?? `${r.name} · ${FREQ[r.frequency] ?? r.frequency} · ${TRANSFORM_LABEL[r.transform] ?? r.transform}`}>
          {ok(r) ? r.name : "UNAVAILABLE"}
        </span>
      ),
    },
    { key: "latest", label: "Last", numeric: true, render: (r) => <span>{fmtUnits(r.latest, r.units)}</span> },
    { key: "c1", label: "1M", numeric: true, value: (r) => r.change?.["1M"] ?? null, render: (r) => <span>{fmtUnits(r.change?.["1M"], r.units, { signed: true, change: true })}</span>, info: INFO.change },
    { key: "c3", label: "3M", numeric: true, hideBelow: 900, value: (r) => r.change?.["3M"] ?? null, render: (r) => <span>{fmtUnits(r.change?.["3M"], r.units, { signed: true, change: true })}</span> },
    { key: "c12", label: "1Y", numeric: true, value: (r) => r.change?.["1Y"] ?? null, render: (r) => <span>{fmtUnits(r.change?.["1Y"], r.units, { signed: true, change: true })}</span> },
    {
      key: "percentile_10y",
      label: "Pctile 10Y",
      numeric: true,
      info: INFO.percentile,
      render: (r) => <PctTick value={r.percentile_10y} label={r.percentile_window ? `${ordinal(r.percentile_10y)} OF ${r.percentile_window.n} OBS · ${fmtDate(r.percentile_window.start, "month")} – ${fmtDate(r.percentile_window.end, "month")}` : undefined} />,
    },
    { key: "spark", label: "3Y", sortable: false, hideBelow: 1200, render: (r) => (r.sparkline && r.sparkline.values.length > 2 ? <Sparkline values={r.sparkline.values} width={72} height={16} color="var(--ink)" area={false} strokeWidth={1} title={`${r.id}, last 3 years`} /> : <span className="mc-dim">—</span>) },
    { key: "date", label: "As of", numeric: true, hideBelow: 900, render: (r) => <span>{r.date ? fmtDate(r.date, "short-year").toUpperCase() : "—"}</span> },
  ];
  return <DataTable<DashboardRow> columns={cols} rows={rows} rowKey={(r) => r.id} compact onRowClick={(r) => ok(r) && onExplore(r.id, r.transform)} />;
}
