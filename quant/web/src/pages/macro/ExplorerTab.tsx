/**
 * Explorer — GET /api/macro/series?ids=&transform=&start=: any FRED series (up to 12) with a
 * transform each (level | y/y % | change | m/m annualized), overlaid or as small multiples,
 * plus a summary table. State lives in the URL (?ids=&tr=&from=) so views are shareable.
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, Panel, SegmentedControl, Select, useTabParam, type Column } from "../../components";
import { fmtDate, fmtNum } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import { INFO, TRANSFORM_LABEL } from "./info";
import { Controls, ordinal, yearsBack } from "./shared";
import type { Dashboard, FredExplorer } from "./types";

const TRANSFORMS = ["level", "yoy_pct", "diff", "mom_ann"] as const;
type Tr = (typeof TRANSFORMS)[number];
const RANGES = ["1", "3", "5", "10", "20", "all"] as const;
const MAX_IDS = 8; // the categorical palette has 8 colours; the API accepts 12

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
  const suggestions = (dashboard?.series ?? []).filter((r) => !ids.includes(r.id));

  return (
    <div className="stack">
      <Controls>
        <form
          className="mc-inline-field"
          onSubmit={(e) => {
            e.preventDefault();
            add(text);
            setText("");
          }}
        >
          <span className="oc-field-label">Add a FRED series id</span>
          <div className="row" style={{ gap: 6 }}>
            <input className="input num" value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. UNRATE, T10Y2Y, CPIAUCSL" aria-label="FRED series id" style={{ width: 240 }} list="mc-fred-ids" />
            <datalist id="mc-fred-ids">
              {suggestions.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </datalist>
            <button type="submit" className="btn btn-sm" disabled={!text.trim() || ids.length >= MAX_IDS}>
              Add
            </button>
          </div>
        </form>
        <div className="mc-inline-field">
          <span className="oc-field-label">Window</span>
          <SegmentedControl size="sm" options={RANGES.map((r) => ({ value: r, label: r === "all" ? "Max" : `${r}Y` }))} value={from} onChange={setFrom} ariaLabel="Window" />
        </div>
        <div className="mc-inline-field">
          <span className="oc-field-label">Layout</span>
          <SegmentedControl size="sm" options={[{ value: "overlay", label: "One chart" }, { value: "multiples", label: "Small multiples" }]} value={layoutMode} onChange={setLayoutMode} ariaLabel="Chart layout" />
        </div>
      </Controls>

      <div className="mc-series-list">
        {ids.map((id, i) => (
          <div key={id} className="mc-series-item">
            <span className="mc-dot" style={{ background: `var(--c${i + 1})` }} />
            <span className="num mc-series-id">{id}</span>
            <span className="subtle small mc-series-name">{q.data?.metadata?.[id]?.title ?? names.get(id)?.name ?? ""}</span>
            <Select<Tr> value={trs[i]} onChange={(t) => setTr(i, t)} options={TRANSFORMS.map((t) => ({ value: t, label: TRANSFORM_LABEL[t] }))} ariaLabel={`Transform for ${id}`} />
            <button type="button" className="icon-btn" onClick={() => remove(i)} aria-label={`Remove ${id}`} title="Remove">
              ×
            </button>
          </div>
        ))}
        {ids.length === 0 && <span className="subtle small">Add a series to begin.</span>}
      </div>
      {suggestions.length > 0 && ids.length < MAX_IDS && (
        <div className="mc-suggest">
          <span className="subtle small">Quick add:</span>
          {suggestions.slice(0, 14).map((r) => (
            <button key={r.id} type="button" className={`mc-chip small ${r.error ? "mc-chip-off" : ""}`} onClick={() => add(r.id, r.transform as Tr)} title={r.error ? `${r.name} — currently unavailable: ${r.error}` : `${r.name} (${TRANSFORM_LABEL[r.transform] ?? r.transform})`}>
              <span className="num">{r.id}</span>
            </button>
          ))}
        </div>
      )}

      <Panel<FredExplorer>
        title="Series"
        subtitle="Official data straight from FRED (Federal Reserve Bank of St. Louis), transformed as chosen. Series are joined on their own observation dates — nothing is forward-filled."
        info={INFO.transforms}
        query={q}
        empty={ids.length === 0}
        skeletonHeight={380}
        notes={[]}
      >
        {(d) => (layoutMode === "overlay" ? <Overlay d={d} /> : <Multiples d={d} />)}
      </Panel>
      <Panel<FredExplorer> title="Summary" subtitle="Latest value, changes and where it sits within the chosen window." info={INFO.percentile} query={q} empty={ids.length === 0} skeletonHeight={160} flush>
        {(d) => <Summary d={d} names={names} />}
      </Panel>
    </div>
  );
}

const lbl = (d: FredExplorer, id: string) => `${id}${d.transforms[id] !== "level" ? ` (${TRANSFORM_LABEL[d.transforms[id]] ?? d.transforms[id]})` : ""}`;

function traces(d: FredExplorer, t: Tokens, only?: string): Data[] {
  return d.ids
    .filter((id) => !only || id === only)
    .map((id) => {
      const i = d.ids.indexOf(id);
      const ys = d.data.data[id] ?? [];
      const xs: (string | number)[] = [];
      const yv: number[] = [];
      d.data.index.forEach((x, j) => {
        const v = ys[j];
        if (v != null) {
          xs.push(x);
          yv.push(v);
        }
      });
      return { type: "scatter", mode: "lines", name: lbl(d, id), x: xs, y: yv, line: { width: 1.6, color: t.categorical[i % 8] }, hovertemplate: `<b>${id}</b> %{y:,.3~f}<extra></extra>` } as Data;
    });
}

function Overlay({ d }: { d: FredExplorer }) {
  const data = useMemo(() => (t: Tokens) => traces(d, t), [d]);
  const layout = useMemo(() => ({ hovermode: "x unified", showlegend: true, xaxis: { type: "date", hoverformat: "%d %b %Y" }, yaxis: { side: "right" }, margin: { l: 16, r: 8, t: 36, b: 28 } }) as any, []);
  return <Chart data={data} layout={layout} height={380} ariaLabel="FRED series" />;
}

function Multiples({ d }: { d: FredExplorer }) {
  return (
    <div className="mc-multiples">
      {d.ids.map((id) => (
        <Multiple key={id} d={d} id={id} />
      ))}
    </div>
  );
}

function Multiple({ d, id }: { d: FredExplorer; id: string }) {
  const data = useMemo(() => (t: Tokens) => traces(d, t, id), [d, id]);
  const layout = useMemo(() => ({ hovermode: "x unified", showlegend: false, xaxis: { type: "date", hoverformat: "%d %b %Y" }, yaxis: { side: "right" }, margin: { l: 8, r: 8, t: 8, b: 24 } }) as any, []);
  return (
    <div>
      <div className="mc-subhead num">{lbl(d, id)}</div>
      <Chart data={data} layout={layout} height={200} compact ariaLabel={id} />
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
    { key: "id", label: "Series", render: (r) => <span className="row" style={{ gap: 8 }}><span className="mc-dot" style={{ background: `var(--c${d.ids.indexOf(r.id) + 1})` }} /><span className="num">{r.id}</span><span className="subtle small">{r.title}</span></span> },
    { key: "tr", label: "Transform", hideBelow: 900 },
    { key: "latest", label: "Latest", numeric: true, format: (v) => n(v) },
    { key: "date", label: "As of", format: (v) => fmtDate(v), hideBelow: 600 },
    { key: "m1", label: "1M", numeric: true, format: (v) => n(v, true), info: INFO.change, hideBelow: 600 },
    { key: "m3", label: "3M", numeric: true, format: (v) => n(v, true), hideBelow: 900 },
    { key: "y1", label: "1Y", numeric: true, format: (v) => n(v, true) },
    { key: "pct", label: "Pct in window", numeric: true, format: (v) => ordinal(v), info: { text: "Share of observations in the chosen window (up to 10 years) at or below the latest value." } },
    { key: "lo", label: "Low", numeric: true, format: (v) => n(v), hideBelow: 1200 },
    { key: "hi", label: "High", numeric: true, format: (v) => n(v), hideBelow: 1200 },
  ];
  return <DataTable columns={cols} rows={rows} rowKey={(r) => r.id} />;
}
