/**
 * Realized volatility (GET /api/options/realized/{ticker}; works offline on committed OHLC):
 * five estimators side by side, the Burghardt–Lane volatility cone with today's readings
 * (and the implied term structure when a chain is available), and the volatility risk premium.
 */
import { useMemo } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, EmptyState, InfoTip, Panel, Section, SegmentedControl, Select, StatGrid, StatTile, TimeSeriesChart, withAlpha, type Column } from "../../components";
import { fmtDate, fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import { frameColumn } from "../../lib/series";
import type { Tokens } from "../../lib/theme";
import { ESTIMATOR_LABEL, ESTIMATOR_SHORT, INFO } from "./info";
import { KV } from "./shared";
import { ESTIMATORS, type ConeRow, type Estimator, type Realized } from "./types";
import type { useRealized } from "./shared";

export const WINDOWS = ["10", "21", "63", "126"] as const;
export const YEARS = ["1", "3", "5", "10"] as const;

/** Range buttons sit top-left; the legend goes under the plot so the two never collide. */
const LEGEND_BELOW = { legend: { x: 0, xanchor: "left", y: -0.12, yanchor: "top" }, margin: { l: 16, r: 8, t: 36, b: 56 } };

const CONE_HORIZON_LABEL: Record<number, string> = { 10: "2W", 21: "1M", 42: "2M", 63: "3M", 126: "6M", 252: "1Y" };

export function RealizedTab({ q, window, setWindow, estimator, setEstimator, years, setYears }: { q: ReturnType<typeof useRealized>; window: string; setWindow: (v: string) => void; estimator: Estimator; setEstimator: (v: Estimator) => void; years: string; setYears: (v: string) => void }) {
  const d = q.data;
  return (
    <>
      <Section
        title="Realized volatility"
        description="How much the price has actually been moving, measured five ways from daily open–high–low–close bars. Range-based estimators squeeze more information out of each day than closing prices alone, so they are less noisy — but they disagree when prices gap overnight."
        actions={
          <>
            <SegmentedControl size="sm" ariaLabel="Rolling window (trading days)" options={WINDOWS.map((w) => ({ value: w, label: `${w}d`, title: `${w}-session rolling window` }))} value={window} onChange={setWindow} />
            <SegmentedControl size="sm" ariaLabel="History" options={YEARS.map((y) => ({ value: y, label: `${y}Y`, title: `${y} years of history` }))} value={years} onChange={setYears} />
          </>
        }
      >
        <div className="vx-grid-main">
          <Panel<Realized>
            title="Estimator comparison"
            subtitle={d ? `Trailing ${d.window}-session volatility, annualized (√252) · ${d.ticker}` : undefined}
            info={{ title: "Five realized-vol estimators", text: "Each line is the same rolling window measured with a different formula. Hover the ? next to each estimator in the table for its formula and paper.", reference: "Parkinson (1980); Garman & Klass (1980); Rogers & Satchell (1991); Yang & Zhang (2000)" }}
            query={q}
            notes={[]}
            skeletonHeight={360}
          >
            {(r) => <EstimatorChart r={r} />}
          </Panel>
          <Panel<Realized>
            title="Today, by window"
            subtitle="Current annualized vol for each estimator and look-back. Shading scales with the level."
            info={{ text: "Rows are estimators, columns are look-back windows in trading days. When the range-based estimators (PK, GK, RS) sit well below close-to-close, overnight gaps are doing much of the moving." }}
            query={q}
            flush
            notes={[]}
            skeletonHeight={360}
          >
            {(r) => <CurrentTable r={r} />}
          </Panel>
        </div>
      </Section>

      <Section
        title="Volatility cone"
        description="Is today's volatility high or low for this asset? For every horizon the bands show where realized vol has spent its time over the selected history; the dots are today's readings. When the live chain is available, implied vol per expiry is overlaid — options priced above the cone look rich, below it cheap."
        actions={
          <Select<Estimator> ariaLabel="Cone estimator" value={estimator} onChange={setEstimator} options={ESTIMATORS.map((e) => ({ value: e, label: `Cone: ${ESTIMATOR_LABEL[e]}` }))} />
        }
      >
        <div className="vx-grid-main">
          <Panel<Realized> title="Cone" subtitle={d ? `${ESTIMATOR_LABEL[d.estimator]} estimator · ${years}-year history to ${fmtDate(d.as_of)}` : undefined} info={INFO.cone} query={q} notes={[]} skeletonHeight={380}>
            {(r) => (r.cone.length ? <ConeChart r={r} /> : <EmptyState title="Not enough history for a cone" />)}
          </Panel>
          <Panel<Realized> title="Where today sits" subtitle="Current value and its percentile among all past windows of that length." info={INFO.cone_pctile} query={q} notes={[]} flush skeletonHeight={380}>
            {(r) => <ConeTable rows={r.cone} />}
          </Panel>
        </div>
      </Section>

      <Section
        title="Volatility risk premium"
        description="Implied volatility is the market's price for future movement; realized is what then happens. The gap is the premium option sellers collect for bearing crash risk — positive most of the time, violently negative in sell-offs."
      >
        <Panel<Realized>
          title="Premium today"
          subtitle={d?.vrp ? `${d.vrp.implied_source} vs trailing ${d.vrp.realized_window}-session realized` : undefined}
          info={INFO.vrp}
          query={q}
          empty={d && !d.vrp ? <EmptyState icon="cloud-off" title={`No implied volatility for ${d.ticker}`}>Neither a live option chain nor a Cboe implied-volatility index for this underlying is available, so there is nothing to compare realized vol against. The notes below give the reason.</EmptyState> : undefined}
          skeletonHeight={110}
        >
          {(r) => r.vrp && <VrpTiles r={r} />}
        </Panel>
        {d?.vrp_history && (
          <div className="grid-2">
            <Panel<Realized> title="Implied vs realized" subtitle={`${d.vrp_history.index} (Cboe, FRED) against trailing 21-session close-to-close realized vol`} info={INFO.vrp} query={q} notes={[]} skeletonHeight={300}>
              {(r) => <VrpLevels r={r} />}
            </Panel>
            <Panel<Realized> title="Premium actually earned" subtitle="Implied vol minus the realized vol of the following 21 sessions. Below zero: realized beat what options priced." info={INFO.vrp_forward} query={q} notes={[]} skeletonHeight={300}>
              {(r) => <VrpForward r={r} />}
            </Panel>
          </div>
        )}
      </Section>
    </>
  );
}

// ------------------------------------------------------------------ charts & tables
function EstimatorChart({ r }: { r: Realized }) {
  const series = useMemo(
    () =>
      ESTIMATORS.map((e) => {
        const c = frameColumn(r.rolling, e);
        return { name: ESTIMATOR_LABEL[e], x: c.x, y: c.y, width: e === "yang_zhang" ? 2 : 1.3 };
      }),
    [r],
  );
  return <TimeSeriesChart series={series} yFormat="pct" digits={1} height={360} rangeSelector layout={{ yaxis: { rangemode: "tozero" }, ...LEGEND_BELOW } as never} />;
}

type CurRow = { est: Estimator } & Record<string, number | null | Estimator>;

function CurrentTable({ r }: { r: Realized }) {
  const wins = Object.keys(r.current).sort((a, b) => +a - +b);
  const rows: CurRow[] = ESTIMATORS.map((e) => ({ est: e, ...Object.fromEntries(wins.map((w) => [w, r.current[w]?.[e] ?? null])) }) as CurRow);
  const all = rows.flatMap((row) => wins.map((w) => row[w] as number | null)).filter((v): v is number => v != null);
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const columns: Column<CurRow>[] = [
    { key: "est", label: "Estimator", sortable: false, render: (row) => <span className="vx-est" title={ESTIMATOR_SHORT[row.est]}>{ESTIMATOR_LABEL[row.est]}<InfoTip info={INFO[row.est]} size={12} /></span> },
    ...wins.map((w) => ({ key: w, label: `${w}d`, numeric: true, sortable: false, format: (v: number | null) => fmtPct(v, 1), heat: { min: lo, max: hi, diverging: false } }) as Column<CurRow>),
  ];
  return <DataTable className="vx-tight" columns={columns} rows={rows} rowKey={(row) => row.est} />;
}

function ConeChart({ r }: { r: Realized }) {
  const cone = r.cone;
  const it = r.implied_term;
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const x = cone.map((c) => c.horizon);
      const band = (lo: keyof ConeRow, hi: keyof ConeRow, alpha: number, name: string) => [
        { type: "scatter", mode: "lines", x, y: cone.map((c) => c[lo]), line: { width: 0, shape: "spline", smoothing: 0.6 }, hoverinfo: "skip", showlegend: false },
        { type: "scatter", mode: "lines", x, y: cone.map((c) => c[hi]), fill: "tonexty", fillcolor: withAlpha(t.categorical[0], alpha), line: { width: 0, shape: "spline", smoothing: 0.6 }, name, hoverinfo: "skip" },
      ];
      const line = (k: keyof ConeRow, name: string, dash: string, color: string, width = 1) => ({ type: "scatter", mode: "lines", x, y: cone.map((c) => c[k]), name, line: { color, width, dash, shape: "spline", smoothing: 0.6 }, hovertemplate: `${name} %{y:.1%}<extra></extra>` });
      const traces: unknown[] = [
        ...band("p10", "p90", 0.13, "10–90th pct"),
        ...band("p25", "p75", 0.22, "25–75th pct"),
        line("p50", "Median", "dash", t.categorical[0], 1.4),
        {
          type: "scatter",
          mode: "lines+markers",
          x,
          y: cone.map((c) => c.current),
          name: "Today",
          line: { color: t.categorical[1], width: 2 },
          marker: { size: 9, color: t.categorical[1], line: { color: t.surface, width: 2 } },
          customdata: cone.map((c) => [c.current_pctile, CONE_HORIZON_LABEL[c.horizon] ?? `${c.horizon}d`]),
          hovertemplate: "<b>Today %{y:.1%}</b> · %{customdata[0]:.0%} pctile<extra>%{x}d (%{customdata[1]})</extra>",
        },
      ];
      if (it && it.dte.length) {
        const pts = it.dte.map((dd, i) => ({ x: (dd ?? NaN) * (252 / 365), y: it.atm_iv[i], d: dd })).filter((p) => Number.isFinite(p.x) && p.y != null && p.x >= 5 && p.x <= 300);
        if (pts.length)
          traces.push({
            type: "scatter",
            mode: "lines+markers",
            x: pts.map((p) => p.x),
            y: pts.map((p) => p.y),
            name: "Implied (ATM, per expiry)",
            line: { color: t.categorical[2], width: 1.5, dash: "dash" },
            marker: { symbol: "diamond", size: 8, color: t.categorical[2] },
            customdata: pts.map((p) => p.d),
            hovertemplate: "<b>Implied %{y:.1%}</b><extra>%{customdata:.0f} calendar days</extra>",
          });
      }
      return traces as Data[];
    },
    [cone, it],
  );
  const layout = useMemo(
    () => ({
      hovermode: "closest",
      xaxis: { type: "log", tickvals: cone.map((c) => c.horizon), ticktext: cone.map((c) => `${c.horizon}d`), title: { text: "Horizon (trading days)" }, showspikes: false },
      yaxis: { tickformat: ".0%", rangemode: "tozero", side: "right" },
      margin: { l: 16, r: 8, t: 36, b: 44 },
    }),
    [cone],
  );
  return <Chart data={data} layout={layout as never} height={380} />;
}

function ConeTable({ rows }: { rows: ConeRow[] }) {
  const columns: Column<ConeRow>[] = [
    { key: "horizon", label: "Horizon", render: (c) => <span className="num">{c.horizon}d <span className="subtle">{CONE_HORIZON_LABEL[c.horizon]}</span></span> },
    { key: "current", label: "Today", numeric: true, format: (v) => fmtPct(v, 1) },
    { key: "p50", label: "Median", numeric: true, render: (c) => <span title={`Range over the history: ${fmtPct(c.min, 1)} – ${fmtPct(c.max, 1)}`}>{fmtPct(c.p50, 1)}</span>, hideBelow: 600 },
    {
      key: "current_pctile",
      label: "Pctile",
      numeric: true,
      info: INFO.cone_pctile,
      render: (c) => (
        <span className="vx-pctile">
          <span className="vx-pctile-bar" aria-hidden>
            <span style={{ left: `${Math.round(c.current_pctile * 100)}%` }} />
          </span>
          <span className={c.current_pctile >= 0.8 ? "loss" : c.current_pctile <= 0.2 ? "gain" : ""}>{fmtNum(c.current_pctile * 100, 0)}</span>
        </span>
      ),
    },
  ];
  return (
    <>
      <DataTable className="vx-tight" columns={columns} rows={rows} rowKey={(c) => c.horizon} />
      <p className="vx-foot-note subtle small">Percentiles use overlapping windows, so they are not independent samples. Vermilion: top quintile (unusually turbulent); green: bottom quintile (unusually calm).</p>
    </>
  );
}

function VrpTiles({ r }: { r: Realized }) {
  const v = r.vrp!;
  const h = r.vrp_history;
  return (
    <div className="stack">
      <StatGrid min={150}>
        <StatTile label="Implied (30d)" value={v.implied} format={(x) => fmtPct(x, 1)} info={v.implied_source.startsWith("30-day ATM") ? INFO.atm_30d : INFO.model_free} caption={v.as_of ? `as of ${fmtDate(v.as_of)}` : v.implied_source.split(" (")[0]} />
        <StatTile label={`Realized (${v.realized_window}d)`} value={v.realized} format={(x) => fmtPct(x, 1)} info={v.estimator === "close_to_close" ? INFO.close_to_close : INFO[v.estimator as Estimator]} caption={ESTIMATOR_LABEL[v.estimator] ?? v.estimator} />
        <StatTile label="Premium" value={v.vol_premium} format={(x) => `${fmtNum(x * 100, 1, { signed: true })} pts`} tone="auto" info={INFO.vrp} caption="implied − realized, vol points" />
        <StatTile label="Implied / realized" value={v.ratio} format={(x) => `${fmtNum(x, 2)}×`} info={INFO.vrp_ratio} />
        {h && <StatTile label="Avg premium earned" value={h.mean_premium_forward} format={(x) => `${fmtNum(x * 100, 1, { signed: true })} pts`} tone="auto" info={INFO.vrp_forward} caption={`over ${h.frame.index.length.toLocaleString()} sessions`} />}
        {h && <StatTile label="Share positive" value={h.share_positive_forward} format={(x) => fmtPct(x, 0)} info={{ text: "Fraction of days on which implied vol ended up above the next 21 sessions' realized vol — how often selling volatility would have paid before costs." }} />}
      </StatGrid>
      {v.close_to_close && v.estimator !== "close_to_close" && (
        <KV
          cols={2}
          rows={[
            { label: "Premium vs close-to-close RV", value: fmtSignedPct(v.close_to_close.vol_premium, 2), info: INFO.close_to_close },
            ...(v.model_free ? [{ label: "Model-free implied − CC RV", value: fmtSignedPct(v.model_free.vol_premium, 2), info: INFO.model_free }] : []),
          ]}
        />
      )}
    </div>
  );
}

function VrpLevels({ r }: { r: Realized }) {
  const series = useMemo(() => {
    const f = r.vrp_history!.frame;
    const imp = frameColumn(f, "implied");
    const rv = frameColumn(f, "trailing_rv");
    return [
      { name: `Implied (${r.vrp_history!.index})`, x: imp.x, y: imp.y },
      { name: "Realized, trailing 21d", x: rv.x, y: rv.y, width: 1.2 },
    ];
  }, [r]);
  return <TimeSeriesChart series={series} yFormat="pct" digits={1} height={300} rangeSelector layout={LEGEND_BELOW as never} />;
}

function VrpForward({ r }: { r: Realized }) {
  const h = r.vrp_history!;
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const c = frameColumn(h.frame, "premium_forward");
      const pos = c.y.map((v) => (v != null && v >= 0 ? v : 0));
      const neg = c.y.map((v) => (v != null && v < 0 ? v : 0));
      return [
        { type: "scatter", mode: "lines", x: c.x, y: pos, fill: "tozeroy", fillcolor: withAlpha(t.gain, 0.35), line: { width: 0 }, name: "Premium earned", hoverinfo: "skip" },
        { type: "scatter", mode: "lines", x: c.x, y: neg, fill: "tozeroy", fillcolor: withAlpha(t.loss, 0.45), line: { width: 0 }, name: "Realized > implied", hoverinfo: "skip" },
        { type: "scatter", mode: "lines", x: c.x, y: c.y, line: { width: 0.8, color: t.text3 }, name: "Forward premium", hovertemplate: "<b>%{y:+.1%}</b><extra></extra>", showlegend: false },
      ] as Data[];
    },
    [h],
  );
  const layout = useMemo(
    () => (t: Tokens) => ({
      hovermode: "x unified",
      xaxis: { type: "date", hoverformat: "%a %d %b %Y" },
      yaxis: { tickformat: "+.0%", side: "right", zeroline: true, zerolinecolor: t.ruleStrong },
      shapes: h.mean_premium_forward != null ? [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: h.mean_premium_forward, y1: h.mean_premium_forward, line: { color: t.text2, width: 1, dash: "dash" } }] : [],
      annotations: h.mean_premium_forward != null ? [{ xref: "paper", yref: "paper", x: 1, y: 1.02, text: `- - mean ${fmtNum(h.mean_premium_forward * 100, 1, { signed: true })} pts`, showarrow: false, xanchor: "right", yanchor: "bottom", font: { size: 11, color: t.text2 } }] : [],
      margin: { l: 16, r: 8, t: 36, b: 28 },
    }),
    [h],
  );
  return <Chart data={data} layout={layout as never} height={300} />;
}
