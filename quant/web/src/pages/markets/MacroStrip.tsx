/**
 * Markets — macro additions near the top of the page:
 *  - RegimeStrip:  GET /api/macro/regimes?ticker=SPY&k=2&freq=W — today's Markov-switching regime,
 *                  a two-year ribbon of the most-likely regime, and the risk-on/off signals.
 *  - RatesRow:     GET /api/macro/curve?compare=1M,1Y — Treasury par curve now vs 1M / 1Y ago,
 *                  next to GET /api/macro/series?ids=DGS2,DGS10 — 2y / 10y / 2s10s (works offline).
 */
import { useMemo } from "react";
import { Link } from "react-router-dom";
import type { Data } from "plotly.js";
import { Chart, InfoTip, Panel, Skeleton, StatGrid, StatTile } from "../../components";
import { isDataUnavailable } from "../../lib/api";
import { fmtBps, fmtDate, fmtNum, fmtPct, fmtPctPoints, fmtSignedPct } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import { useCurve } from "../macro/CurveTab";
import { INFO } from "../macro/info";
import { regimeColors, regimeNames, useRegimes } from "../macro/RegimesTab";
import { tenorAxis, tenorLabel } from "../macro/shared";
import type { CurveOut, FredExplorer, RegimesOut } from "../macro/types";

// ------------------------------------------------------------------ regime strip
export function RegimeStrip() {
  const q = useRegimes("SPY", 2, "W");
  if (q.isLoading) return <Skeleton height={88} />;
  if (q.isError || !q.data) {
    return (
      <div className="mk-regime mk-regime-off" role="status">
        <span className="eyebrow">Market regime</span>
        <span className="subtle small">{isDataUnavailable(q.error) ? `Unavailable — ${q.error.detail}` : "The regime model could not be estimated right now."}</span>
      </div>
    );
  }
  return <RegimeStripBody d={q.data} />;
}

function RegimeStripBody({ d }: { d: RegimesOut }) {
  const names = regimeNames(d.k);
  const cols = regimeColors(null, d.k);
  const cur = d.model.current_regime;
  const r = d.regimes[cur];
  // last ~2 years of the most-likely regime (smoothed), one cell per week
  const ribbon = useMemo(() => {
    const f = d.smoothed;
    const n = f.index.length;
    const from = Math.max(0, n - 104);
    return f.index.slice(from).map((date, i) => {
      const j = from + i;
      let best = 0;
      let bv = -1;
      f.columns.forEach((c, k) => {
        const v = (f.data[c][j] as number | null) ?? -1;
        if (v > bv) {
          bv = v;
          best = k;
        }
      });
      return { date: String(date), k: best, p: bv };
    });
  }, [d]);
  const comps = d.risk_panel.components;
  const tone = cur === 0 ? "gain" : cur === d.k - 1 ? "loss" : "warn";
  return (
    <section className="mk-regime" aria-label="Market regime">
      <div className="mk-regime-now">
        <span className="eyebrow">
          Market regime · {d.ticker} weekly <InfoTip info={INFO.markov} size={12} />
        </span>
        <div className="mk-regime-state">
          <span className={`mk-regime-name ${tone}`}>{names[cur]}</span>
          <span className="num subtle">{fmtPct(d.model.current_prob, 0)}</span>
        </div>
        <span className="subtle small">
          typical <span className="num">{fmtSignedPct(r.mean_ann, 0)}</span> return, <span className="num">{fmtPct(r.vol_ann, 0)}</span> vol a year
        </span>
      </div>
      <div className="mk-regime-ribbon-wrap">
        <div className="mk-regime-ribbon" role="img" aria-label="Most-likely regime by week, last two years">
          {ribbon.map((c) => (
            <span key={c.date} style={{ background: cols[c.k], opacity: 0.35 + 0.65 * Math.max(0, c.p) }} title={`${fmtDate(c.date)} · ${names[c.k]} ${fmtPct(c.p, 0)}`} />
          ))}
        </div>
        <div className="mk-regime-axis subtle">
          <span>{fmtDate(ribbon[0]?.date, "month")}</span>
          <span>
            {names.map((n, i) => (
              <span key={n} className="mk-regime-key">
                <span className="mk-regime-dot" style={{ background: cols[i] }} />
                {n}
              </span>
            ))}
          </span>
          <span>{fmtDate(ribbon[ribbon.length - 1]?.date, "month")}</span>
        </div>
      </div>
      <div className="mk-regime-signals">
        {comps.map((c) => (
          <div key={c.name} className="mk-regime-signal" title={`${c.label}: ${c.rule}`}>
            <span className={`mk-regime-dot ${c.score > 0.5 ? "on" : c.score < 0.5 ? "off" : ""}`} />
            <span className="small">{c.name === "vix" ? "VIX" : c.name === "hy_oas" ? "HY spread" : c.name === "curve_slope" ? "Curve" : "Trend"}</span>
            <span className="num small subtle">{c.name === "vix" ? fmtNum(c.value, 1) : c.name === "curve_slope" ? `${fmtNum(c.value, 2)} pp` : c.name === "trend" ? fmtSignedPct(c.value, 1) : fmtPctPoints(c.value)}</span>
          </div>
        ))}
        <Link to="/macro?tab=regimes" className="mk-regime-link small">
          Regimes →
        </Link>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ rates row
export function RatesRow() {
  const curve = useCurve(undefined, ["1M", "1Y"]);
  const tsy = useApiQuery<FredExplorer>("/macro/series", { ids: "DGS2,DGS10", start: "2024-01-01" });
  return (
    <div className="grid-3">
      <Panel<CurveOut>
        title="Yield curve today vs 1M / 1Y"
        subtitle="Treasury yields by maturity. Upward-sloping is normal; when short yields sit above long ones (inversion) markets expect rate cuts — historically a recession warning."
        info={INFO.par}
        query={curve}
        span={2}
        compact
        skeletonHeight={300}
        notes={[]}
        actions={
          <Link to="/macro?tab=curve" className="btn btn-sm btn-ghost">
            Curve analysis →
          </Link>
        }
      >
        {(d) => <MiniCurve d={d} />}
      </Panel>
      <Panel<FredExplorer> title="Treasuries" subtitle="The two most-watched points on the curve and the slope between them." info={INFO.s2s10} query={tsy} compact skeletonHeight={300} notes={[]}>
        {(d) => <TreasuryTiles d={d} />}
      </Panel>
    </div>
  );
}

function MiniCurve({ d }: { d: CurveOut }) {
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const ok = d.compare.filter((c) => !c.error && c.par);
      const tr = (c: { par?: { tenors: number[]; yields: number[] } }, name: string, color: string, main: boolean) =>
        ({ type: "scatter", mode: "lines+markers", name, x: c.par!.tenors, y: c.par!.yields, customdata: c.par!.tenors.map(tenorLabel), line: { color, width: main ? 2.4 : 1.5, dash: main ? "solid" : "dot", shape: "spline", smoothing: 0.6 }, marker: { size: main ? 6 : 4, color }, hovertemplate: "%{customdata}  <b>%{y:.2f}%</b><extra>%{fullData.name}</extra>" }) as Data;
      return [...ok.map((c, i) => tr(c, `${c.label} ago`, t.categorical[i + 1], false)), tr(d, `Today · ${fmtDate(d.date, "short")}`, t.categorical[0], true)];
    },
    [d],
  );
  const layout = useMemo(() => ({ xaxis: { ...tenorAxis(), title: undefined }, yaxis: { ticksuffix: "%", tickformat: ".2f", side: "right" }, hovermode: "x unified", legend: { traceorder: "reversed" }, margin: { l: 8, r: 8, t: 30, b: 28 } }) as any, []);
  return <Chart data={data} layout={layout} height={280} ariaLabel="Treasury yield curve now vs 1 month and 1 year ago" />;
}

function TreasuryTiles({ d }: { d: FredExplorer }) {
  const s2 = d.summary.DGS2;
  const s10 = d.summary.DGS10;
  // 2s10s from the same-dated observations in the window
  const spread = useMemo(() => {
    const a = d.data.data.DGS2 ?? [];
    const b = d.data.data.DGS10 ?? [];
    for (let i = d.data.index.length - 1; i >= 0; i--) if (a[i] != null && b[i] != null) return { v: 100 * ((b[i] as number) - (a[i] as number)), date: String(d.data.index[i]) };
    return null;
  }, [d]);
  const bp = (v: number) => fmtBps(v / 1e4, 0, { signed: true });
  return (
    <StatGrid min={130}>
      <StatTile label="2-year" value={fmtPctPoints(s2?.latest)} caption={s2 ? `${s2.change["1M"] != null ? bp(s2.change["1M"]! * 100) : "—"} 1M · ${s2.change["1Y"] != null ? bp(s2.change["1Y"]! * 100) : "—"} 1Y` : undefined} />
      <StatTile label="10-year" value={fmtPctPoints(s10?.latest)} caption={s10 ? `${s10.change["1M"] != null ? bp(s10.change["1M"]! * 100) : "—"} 1M · ${s10.change["1Y"] != null ? bp(s10.change["1Y"]! * 100) : "—"} 1Y` : undefined} />
      <StatTile label="2s10s" value={spread ? bp(spread.v) : null} tone={spread && spread.v < 0 ? "loss" : "neutral"} info={INFO.s2s10} caption={spread ? (spread.v < 0 ? "inverted" : "positive slope") + ` · ${fmtDate(spread.date, "short")}` : undefined} />
    </StatGrid>
  );
}
