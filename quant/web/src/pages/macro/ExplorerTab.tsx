/**
 * EXPLORER — GET /api/macro/series?ids=&transform=&start=: any FRED series (up to 8) with a
 * transform each (level | y/y % | change | m/m annualized), overlaid or as small multiples, and
 * a summary table. Series are joined on their own observation dates; nothing is forward-filled.
 * State lives in the URL (?ids=&tr=&from=).
 */
import { useMemo, useState } from "react";
import { DataTable, Panel, SegmentedControl, Select, useTabParam, type Column } from "../../components";
import { XYChart } from "../../charts/XYChart";
import { alignSeries } from "../../charts/scales";
import { Note } from "../../design";
import { fmtDate, fmtNum } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import { seriesStyle } from "./derive";
import { INFO, TRANSFORM_LABEL } from "./info";
import { Controls, Ctl, PctTick, yearsBack } from "./shared";
import type { Dashboard, FredExplorer } from "./types";

const TRANSFORMS = ["level", "yoy_pct", "diff", "mom_ann"] as const;
type Tr = (typeof TRANSFORMS)[number];
const RANGES = ["1", "3", "5", "10", "20", "all"] as const;
const MAX_IDS = 8;

export function ExplorerTab({ dashboard }: { dashboard?: Dashboard }) {
  const [idsParam, setIdsParam] = useTabParam<string>("ids", "DGS10,DGS2");
  const [trParam, setTrParam] = useTabParam<string>("tr", "");
  const [from, setFrom] = useTabParam<string>("from", "10");
  const [layoutMode, setLayoutMode] = useState<"overlay" | "multiples">("overlay");
  const [text, setText] = useState("");

  const ids = useMemo(() => idsParam.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean).slice(0, MAX_IDS), [idsParam]);
  const trs = useMemo(() => {
    const t = trParam.split(",");
    return ids.map((_, i) => ((TRANSFORMS as readonly string[]).includes(t[i]) ? (t[i] as Tr) : "level"));
  }, [trParam, ids]);
  const setBoth = (nextIds: string[], nextTr: Tr[]) => {
    setIdsParam(nextIds.join(","));
    setTrParam(nextTr.every((t) => t === "level") ? "" : nextTr.join(","));
  };
  const add = (raw: string, tr: Tr = "level") => {
    const id = raw.trim().toUpperCase().replace(/[^A-Z0-9_.-]/g, "");
    if (!id || ids.includes(id) || ids.length >= MAX_IDS) return;
    setBoth([...ids, id], [...trs, tr]);
  };
  const remove = (i: number) => setBoth(ids.filter((_, j) => j !== i), trs.filter((_, j) => j !== i));
  const setTr = (i: number, t: Tr) => setBoth(ids, trs.map((x, j) => (j === i ? t : x)));

  const start = from === "all" ? undefined : yearsBack(+from);
  const q = useApiQuery<FredExplorer>("/macro/series", { ids: ids.join(","), transform: trs.join(","), start }, { enabled: ids.length > 0 });
  const names = useMemo(() => new Map((dashboard?.series ?? []).map((r) => [r.id, r])), [dashboard]);
  const suggestions = (dashboard?.series ?? []).filter((r) => !ids.includes(r.id) && !r.error);
  const asOf = q.data?.data.index.length ? String(q.data.data.index[q.data.data.index.length - 1]) : undefined;

  return (
    <div className="stack">
      <Controls>
        <form
          className="mc-ctl"
          onSubmit={(e) => {
            e.preventDefault();
            add(text);
            setText("");
          }}
        >
          <span className="mc-ctl-k">ADD</span>
          <input className="input num mc-id-input" value={text} onChange={(e) => setText(e.target.value.toUpperCase())} placeholder="FRED ID" aria-label="FRED series id" list="mc-fred-ids" />
          <datalist id="mc-fred-ids">
            {suggestions.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </datalist>
          <button type="submit" className="btn btn-sm" disabled={!text.trim() || ids.length >= MAX_IDS}>
            ADD
          </button>
        </form>
        <Ctl label="WINDOW">
          <SegmentedControl size="sm" options={RANGES.map((r) => ({ value: r, label: r === "all" ? "MAX" : `${r}Y` }))} value={from} onChange={setFrom} ariaLabel="Window" />
        </Ctl>
        <Ctl label="LAYOUT">
          <SegmentedControl size="sm" options={[{ value: "overlay", label: "ONE" }, { value: "multiples", label: "SMALL ×" }]} value={layoutMode} onChange={setLayoutMode} ariaLabel="Chart layout" />
        </Ctl>
      </Controls>

      <div className="mc-series">
        {ids.map((id, i) => (
          <div key={id} className="mc-series-row">
            <span className={`mc-key mc-key-${seriesStyle(i).tone} mc-key-${seriesStyle(i).dash}`} aria-hidden />
            <span className="num mc-series-id">{id}</span>
            <span className="mc-series-name">{q.data?.metadata?.[id]?.title ?? names.get(id)?.name ?? ""}</span>
            <Select<Tr> value={trs[i]} onChange={(t) => setTr(i, t)} options={TRANSFORMS.map((t) => ({ value: t, label: TRANSFORM_LABEL[t] }))} ariaLabel={`Transform for ${id}`} />
            <button type="button" className="btn btn-sm mc-x" onClick={() => remove(i)} aria-label={`Remove ${id}`}>
              ×
            </button>
          </div>
        ))}
        {ids.length === 0 && <div className="mc-none num">NO SERIES · ADD A FRED ID</div>}
        {suggestions.length > 0 && ids.length < MAX_IDS && (
          <div className="mc-suggest">
            <span className="mc-ctl-k">QUICK</span>
            {suggestions.slice(0, 14).map((r) => (
              <button key={r.id} type="button" className="mc-chip num" onClick={() => add(r.id, r.transform as Tr)} title={`${r.name} · ${TRANSFORM_LABEL[r.transform] ?? r.transform}`}>
                {r.id}
              </button>
            ))}
          </div>
        )}
      </div>

      <Panel<FredExplorer>
        title={
          <>
            SERIES · FRED
            <Note n={1} to="macro" />
          </>
        }
        query={q}
        empty={ids.length === 0}
        skeletonHeight={340}
        notes={[]}
        asOf={asOf}
      >
        {(d) => (layoutMode === "overlay" ? <Overlay d={d} /> : <Multiples d={d} />)}
      </Panel>
      <Panel<FredExplorer> title="SUMMARY" query={q} empty={ids.length === 0} skeletonHeight={160} flush provenance={[]} asOf={asOf}>
        {(d) => <Summary d={d} names={names} />}
      </Panel>
    </div>
  );
}

const lbl = (d: FredExplorer, id: string) => `${id}${d.transforms[id] && d.transforms[id] !== "level" ? ` ${TRANSFORM_LABEL[d.transforms[id]] ?? d.transforms[id]}` : ""}`;

function Overlay({ d }: { d: FredExplorer }) {
  const a = useMemo(() => alignSeries(d.ids.map((id) => ({ x: d.data.index, y: d.data.data[id] ?? [] }))), [d]);
  return (
    <XYChart
      x={a.t.map((s) => s * 1000)}
      time
      series={d.ids.map((id, i) => ({ name: lbl(d, id), y: a.ys[i], ...seriesStyle(i), span: true }))}
      yFormat="num"
      digits={2}
      height={340}
      ariaLabel={`FRED series ${d.ids.join(", ")}`}
    />
  );
}

function Multiples({ d }: { d: FredExplorer }) {
  return (
    <div className="mc-multiples">
      {d.ids.map((id) => (
        <div key={id}>
          <h4 className="mc-sub num">{lbl(d, id)}</h4>
          <XYChart x={d.data.index} time series={[{ name: id, y: d.data.data[id] ?? [], tone: "ink", span: true }]} yFormat="num" digits={2} height={180} ariaLabel={`FRED series ${id}`} />
        </div>
      ))}
    </div>
  );
}

function Summary({ d, names }: { d: FredExplorer; names: Map<string, { name: string }> }) {
  type Row = { id: string; title: string; tr: string; latest?: number; date?: string; m1?: number | null; m3?: number | null; y1?: number | null; pct?: number; lo?: number; hi?: number };
  const rows: Row[] = d.ids.map((id) => {
    const s = d.summary[id];
    return { id, title: d.metadata?.[id]?.title ?? names.get(id)?.name ?? "", tr: TRANSFORM_LABEL[d.transforms[id]] ?? d.transforms[id], latest: s?.latest, date: s?.date, m1: s?.change["1M"], m3: s?.change["3M"], y1: s?.change["1Y"], pct: s?.percentile_10y, lo: s?.history_min, hi: s?.history_max };
  });
  const n = (v: number | null | undefined, signed = false) => fmtNum(v, Math.abs(v ?? 0) >= 1000 ? 0 : 2, { signed });
  const cols: Column<Row>[] = [
    { key: "id", label: "Series", render: (r) => <span className="num">{r.id}</span> },
    { key: "title", label: "Name", hideBelow: 900, render: (r) => <span>{r.title}</span> },
    { key: "tr", label: "Tr", hideBelow: 600, render: (r) => <span className="num">{r.tr}</span> },
    { key: "latest", label: "Last", numeric: true, format: (v) => n(v) },
    { key: "date", label: "As of", numeric: true, hideBelow: 600, format: (v) => (v ? fmtDate(v, "short-year").toUpperCase() : "—") },
    { key: "m1", label: "1M", numeric: true, format: (v) => n(v, true), info: INFO.change, hideBelow: 600 },
    { key: "m3", label: "3M", numeric: true, format: (v) => n(v, true), hideBelow: 900 },
    { key: "y1", label: "1Y", numeric: true, format: (v) => n(v, true) },
    { key: "pct", label: "Pctile", numeric: true, render: (r) => <PctTick value={r.pct} />, info: { title: "Percentile in window", text: "Share of observations in the window (up to 10 years) at or below the latest value." } },
    { key: "lo", label: "Low", numeric: true, format: (v) => n(v), hideBelow: 1200 },
    { key: "hi", label: "High", numeric: true, format: (v) => n(v), hideBelow: 1200 },
  ];
  return <DataTable<Row> columns={cols} rows={rows} rowKey={(r) => r.id} compact />;
}
