/**
 * FRONTIER: the constrained efficient frontier in (vol, return) with the capital market line,
 * the assets, the other methods, the current portfolio and the chosen optimum, each labelled
 * on the map; the same portfolios as a ruled table; and the weights along the frontier.
 * Every figure is ex-ante (the model's own estimates).
 */
import { useMemo, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { DataTable, Toggle, type Column } from "../../components";
import { MapChart, type MapCurve, type MapPoint } from "../../charts/MapChart";
import { Note } from "../../design";
import type { ApiError } from "../../lib/api";
import { fmtNum, fmtPct } from "../../lib/format";
import { frontierSlices } from "./derive";
import { INFO, METHOD_CODE } from "./info";
import { QPanel, sortBy } from "./shared";
import type { FrontierOut, MethodName, OptimizeOut } from "./types";

type Mark = { ret: number | null; vol: number; sharpe: number | null; method?: MethodName };

export function FrontierTab({ q, opt, current }: { q: UseQueryResult<FrontierOut, ApiError>; opt: UseQueryResult<OptimizeOut, ApiError>; current: UseQueryResult<OptimizeOut, ApiError> | null }) {
  const [showAssets, setShowAssets] = useState(true);
  const [showMethods, setShowMethods] = useState(true);
  const chosen: Mark | null = opt.data && !opt.isError ? { ret: opt.data.result.expected_return, vol: opt.data.result.volatility, sharpe: opt.data.result.sharpe, method: opt.data.result.method } : null;
  const cur: Mark | null = current?.data && !current.isError ? { ret: current.data.result.expected_return, vol: current.data.result.volatility, sharpe: current.data.result.sharpe } : null;
  const asOf = q.data?.universe.end;

  return (
    <div className="op-tab-body">
      <QPanel<FrontierOut>
        q={q}
        title={
          <>
            Frontier · ex-ante
            <Note n={4} to="mean-variance" />
          </>
        }
        asOf={asOf}
        skeletonHeight={460}
        actions={
          <>
            <Toggle label="ASSETS" checked={showAssets} onChange={setShowAssets} />
            <Toggle label="METHODS" checked={showMethods} onChange={setShowMethods} />
          </>
        }
      >
        {(d) => <Map d={d} chosen={chosen} current={cur} showAssets={showAssets} showMethods={showMethods} />}
      </QPanel>

      {!q.isError && (
        <div className="op-fr-grid">
          <QPanel<FrontierOut> q={q} title="Portfolios" asOf={asOf} flush skeletonHeight={260} notes={[]} provenance={[]}>
            {(d) => <PointsTable d={d} chosen={chosen} current={cur} />}
          </QPanel>
          <QPanel<FrontierOut> q={q} title="Weights along frontier" asOf={asOf} flush skeletonHeight={260} notes={[]} provenance={[]}>
            {(d) => <Composition d={d} />}
          </QPanel>
        </div>
      )}
    </div>
  );
}

const sr = (s: number | null | undefined): [string, string][] => (s == null ? [] : [["SR", fmtNum(s, 2)]]);

function Map({ d, chosen, current, showAssets, showMethods }: { d: FrontierOut; chosen: Mark | null; current: Mark | null; showAssets: boolean; showMethods: boolean }) {
  const { curves, points, rest } = useMemo(() => {
    const curves: MapCurve[] = [{ name: "FRONTIER", pts: d.frontier.map((p) => ({ x: p.vol, y: p.ret })), heavy: true }];
    if (d.cml && d.tangency) curves.push({ name: "CML", pts: d.cml.map((p) => ({ x: p.vol, y: p.ret })), dash: true });
    const pts: MapPoint[] = [];
    // most important first: label placement is greedy
    if (chosen?.ret != null) pts.push({ name: `CHOSEN · ${chosen.method ? METHOD_CODE[chosen.method] : ""}`, label: "CHOSEN", x: chosen.vol, y: chosen.ret, mark: "frame", extra: sr(chosen.sharpe) });
    if (current?.ret != null) pts.push({ name: "CURRENT", x: current.vol, y: current.ret, mark: "cross", extra: sr(current.sharpe) });
    if (d.tangency) pts.push({ name: "TANGENCY", label: "TAN", x: d.tangency.vol, y: d.tangency.ret, mark: "fill", extra: sr(d.tangency.sharpe) });
    const gmvInOverlay = showMethods && d.overlay.some((o) => o.method === "min_variance" && o.vol != null);
    if (!gmvInOverlay) pts.push({ name: "GLOBAL MIN VAR", label: "GMV", x: d.gmv.vol, y: d.gmv.ret, mark: "fill" });
    if (showMethods) for (const o of d.overlay) if (o.ret != null && o.vol != null) pts.push({ name: METHOD_CODE[o.method], x: o.vol, y: o.ret, mark: "dim", extra: sr(o.sharpe) });
    if (showAssets) for (const a of d.assets) pts.push({ name: a.ticker, x: a.vol, y: a.ret, mark: "open" });
    return { curves, points: pts, rest: pts[0]?.name };
  }, [d, chosen, current, showAssets, showMethods]);
  return <MapChart curves={curves} points={points} rest={rest} xTitle="VOL" yTitle="RET" height={460} ariaLabel="Efficient frontier with assets and allocation methods" />;
}

type PRow = { key: string; name: string; ret: number | null; vol: number | null; sharpe: number | null; strong?: boolean; error?: string };

function PointsTable({ d, chosen, current }: { d: FrontierOut; chosen: Mark | null; current: Mark | null }) {
  const rows = useMemo<PRow[]>(() => {
    const out: PRow[] = [];
    if (chosen?.ret != null) out.push({ key: "chosen", name: `CHOSEN · ${chosen.method ? METHOD_CODE[chosen.method] : ""}`, ret: chosen.ret, vol: chosen.vol, sharpe: chosen.sharpe, strong: true });
    if (current?.ret != null) out.push({ key: "current", name: "CURRENT", ret: current.ret, vol: current.vol, sharpe: current.sharpe, strong: true });
    if (d.tangency) out.push({ key: "tangency", name: "TANGENCY", ret: d.tangency.ret, vol: d.tangency.vol, sharpe: d.tangency.sharpe ?? null });
    if (!d.overlay.some((o) => o.method === "min_variance" && o.vol != null))
      out.push({ key: "gmv", name: "GLOBAL MIN VAR", ret: d.gmv.ret, vol: d.gmv.vol, sharpe: d.tangency && d.risk_free.annual != null ? (d.gmv.ret - d.risk_free.annual) / d.gmv.vol : null });
    for (const o of d.overlay) out.push({ key: o.method, name: METHOD_CODE[o.method], ret: o.ret ?? null, vol: o.vol ?? null, sharpe: o.sharpe ?? null, error: o.error });
    return out;
  }, [d, chosen, current]);
  const cols: Column<PRow>[] = [
    {
      key: "name",
      label: "Portfolio",
      render: (r) => (
        <span className={`num ${r.strong ? "op-strong" : ""}`} title={r.error}>
          {r.name}
          {r.error && <span className="loss"> · FAILED</span>}
        </span>
      ),
    },
    { key: "ret", label: "Ret", numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), info: INFO.exAnteReturn },
    { key: "vol", label: "Vol", numeric: true, format: (v) => fmtPct(v, 1), info: INFO.exAnteVol },
    { key: "sharpe", label: "SR", numeric: true, format: (v) => fmtNum(v, 2), info: INFO.exAnteSharpe },
  ];
  return <DataTable columns={cols} rows={rows} rowKey={(r) => r.key} />;
}

type CRow = { ticker: string; w: number[] };

/** Rows are assets, columns seven frontier portfolios from lowest to highest vol; cells shade with weight. */
function Composition({ d }: { d: FrontierOut }) {
  const slices = useMemo(() => frontierSlices(d.frontier, 7), [d]);
  const order = useMemo(() => sortBy(d.universe.tickers, Object.fromEntries(d.universe.tickers.map((t) => [t, Math.max(...slices.map((p) => Math.abs(p.weights[t] ?? 0)))]))), [d, slices]);
  const rows: CRow[] = order.map((t) => ({ ticker: t, w: slices.map((p) => p.weights[t] ?? 0) }));
  const hi = Math.max(0.01, ...rows.flatMap((r) => r.w.map(Math.abs)));
  const cols: Column<CRow>[] = [
    { key: "ticker", label: "Vol →", render: (r) => <span className="num">{r.ticker}</span>, sortable: false },
    ...slices.map(
      (p, i) =>
        ({
          key: `s${i}`,
          label: fmtPct(p.vol, 1),
          title: `Ret ${fmtPct(p.ret, 1)}`,
          numeric: true,
          sortable: false,
          // a zero weight is a dot with no tint, so the shading reads as the holdings alone
          value: (r: CRow) => (Math.abs(r.w[i]) < 0.0005 ? null : r.w[i]),
          format: (v: number | null) => (v == null ? "·" : fmtPct(v, 0)),
          heat: { min: 0, max: hi, diverging: false },
          hideBelow: i % 2 === 1 ? 600 : undefined,
        }) as Column<CRow>,
    ),
  ];
  return <DataTable columns={cols} rows={rows} rowKey={(r) => r.ticker} />;
}
