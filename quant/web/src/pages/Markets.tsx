/**
 * Markets (/markets) — the REFERENCE PAGE for Paper Tape. Read this before migrating a page.
 *
 *  1. Data via `useApiQuery` with typed payloads in a page-local module (./markets/data.ts).
 *  2. Every block is a Cell (<Panel query={q}>): loading, 503 DATA UNAVAILABLE, notes and the
 *     provenance line come from Panel; `asOf` goes in the header.
 *  3. Labels, not sentences: Archivo caps titles (`CURVE · UST PAR`), no subtitle or
 *     description strings, no question-shaped headers. Explanation lives in Methodology.
 *  4. Numbers only through lib/format, rendered in `.num` (Plex Mono, tabular). Gains ink with
 *     a "+", losses --signal (`signClass`, DataTable `color: "sign"`). No green.
 *  5. InfoTips only on metric labels (2S10S, 1D, YTD, VOL 1M, 52W RANGE, CORRELATION).
 *  6. No `toFixed`, no inline style except CSS custom properties (ESLint enforces both).
 *  7. Charts are the uPlot wrappers in src/charts (or components' re-exports); never Plotly.
 *  8. Page controls in <Page actions>; shareable state in the URL (`useTabParam`).
 *  9. Page-local components and CSS in ./markets/ (classes prefixed `mk-`).
 */
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { BarChart, HeatmapChart, Page, Panel, Section, SegmentedControl, Skeleton, StatGrid, useTabParam } from "../components";
import { fmtStamp } from "../design/stamp";
import { prettyName, useUniverses } from "../lib/market";
import { useApiQuery } from "../lib/query";
import { IndexFigure } from "./markets/IndexFigure";
import { MarketTable } from "./markets/MarketTable";
import { Rates } from "./markets/Rates";
import { SectorHeatmap } from "./markets/SectorHeatmap";
import { PERIODS, allFailed, periodOf, useOverview, type Overview, type PeriodKey } from "./markets/data";
import "./markets/markets.css";

/** Universe keys given dedicated treatment; everything else becomes a table. */
const HERO = "us_equity_indices";
const SECTORS = "sectors";
const VOL = "volatility";
/** A small cross-asset basket for the correlation matrix and the period bar chart. */
const CROSS_ASSET = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG", "GLD", "USO", "BTC-USD"];

type OverviewQuery = ReturnType<typeof useOverview>;

export default function Markets() {
  const [periodLabel, setPeriod] = useTabParam<string>("period", "1D");
  const period = periodOf(periodLabel);
  const universes = useUniverses();
  const health = useApiQuery<{ offline?: boolean }>("/health", undefined, { staleTime: Infinity });

  const hero = useOverview(HERO);
  const vix = useOverview(VOL);
  const sectors = useOverview(SECTORS);
  const cross = useOverview(null, CROSS_ASSET);

  const groups = (universes.data ?? []).filter((u) => ![HERO, SECTORS, VOL].includes(u.name));
  const asOf = [hero.data?.as_of, sectors.data?.as_of, cross.data?.as_of].filter((x): x is string => !!x).sort().pop();
  const sectorError = sectors.error ?? allFailed(sectors.data);

  return (
    <Page
      title="Markets"
      meta={
        asOf || health.data?.offline ? (
          <>
            {asOf && <span>CLOSE {fmtStamp(asOf)}</span>}
            {health.data?.offline && <span>OFFLINE DATASET</span>}
          </>
        ) : undefined
      }
      actions={<SegmentedControl ariaLabel="Return horizon" options={PERIODS.map((p) => ({ value: p.label, label: p.label, title: `${p.long} return` }))} value={period.label} onChange={setPeriod} />}
    >
      <Indices hero={hero} vix={vix} periodKey={period.key} periodLabel={period.label} />

      <Section title="Rates">
        <Rates />
      </Section>

      <Section title={`Sectors · ${period.label}`}>
        <div className="grid-3">
          <Panel<Overview> title={`Heatmap · ±${Math.round(period.scale * 100)}%`} query={sectors} error={sectorError} span={2} skeletonHeight={300} asOf={sectors.data?.as_of ?? undefined}>
            {(d) => <SectorHeatmap rows={d.rows} period={period.key} scale={period.scale} />}
          </Panel>
          <Panel<Overview> notes={[]} title={`Ranked · ${period.label}`} query={sectors} error={sectorError} skeletonHeight={300} asOf={sectors.data?.as_of ?? undefined}>
            {(d) => {
              const rows = ranked(d, period.key);
              return <BarChart horizontal colorBySign x={rows.map((r) => r.name)} y={rows.map((r) => r[period.key] as number)} yFormat="pct" height={Math.max(180, rows.length * 26 + 40)} />;
            }}
          </Panel>
        </div>
        <Panel<Overview> notes={[]} title="Detail" query={sectors} error={sectorError} flush skeletonHeight={240} asOf={sectors.data?.as_of ?? undefined}>
          {(d) => <MarketTable rows={d.rows} />}
        </Panel>
      </Section>

      <Section title="Cross-asset">
        {universes.isLoading && <Skeleton height={240} />}
        <div className="mk-groups">
          {universes.isError && <Panel title="Universes" error={universes.error} onRetry={() => universes.refetch()} />}
          {groups.map((u) => (
            <GroupPanel key={u.name} name={u.name} label={u.label || prettyName(u.name)} />
          ))}
        </div>
      </Section>

      <Section title="Basket">
        <div className="grid-2">
          <BasketBars q={cross} periodKey={period.key} periodLabel={period.label} />
          <CorrelationPanel q={cross} />
        </div>
      </Section>
    </Page>
  );
}

function ranked(d: Overview, key: PeriodKey) {
  return d.rows.filter((r) => !r.error && r[key] != null).sort((a, b) => (b[key] as number) - (a[key] as number));
}

function Indices({ hero, vix, periodKey, periodLabel }: { hero: OverviewQuery; vix: OverviewQuery; periodKey: PeriodKey; periodLabel: string }) {
  if (hero.isLoading) return <Skeleton height={132} />;
  if (hero.isError) return <Panel title="Indices" error={hero.error} onRetry={() => hero.refetch()} />;
  const rows = [...(hero.data?.rows ?? []), ...(vix.data?.rows ?? [])];
  return (
    <section className="mk-indices" aria-label="Indices">
      <StatGrid min={150}>
        {rows.map((r) => (
          <IndexFigure key={r.ticker} row={r} period={periodKey} periodLabel={periodLabel} level={r.ticker.startsWith("^")} />
        ))}
      </StatGrid>
    </section>
  );
}

function GroupPanel({ name, label }: { name: string; label: string }) {
  const q = useOverview(name);
  const failed = allFailed(q.data);
  const ok = q.data?.rows.filter((r) => !r.error).length ?? 0;
  const missing = q.data && !failed ? q.data.rows.length - ok : 0;
  return (
    <Panel<Overview>
      title={label}
      query={q}
      error={q.error ?? failed}
      compact
      flush
      skeletonHeight={160}
      asOf={q.data?.as_of ?? undefined}
      actions={missing > 0 ? <span className="mk-missing num">{missing}/{q.data!.rows.length} MISSING</span> : undefined}
    >
      {(d) => <MarketTable rows={d.rows} />}
    </Panel>
  );
}

function BasketBars({ q, periodKey, periodLabel }: { q: OverviewQuery; periodKey: PeriodKey; periodLabel: string }) {
  const nav = useNavigate();
  return (
    <Panel<Overview> title={`Return · ${periodLabel}`} query={q} error={q.error ?? allFailed(q.data)} skeletonHeight={320} asOf={q.data?.as_of ?? undefined}>
      {(d) => {
        const rows = ranked(d, periodKey);
        const missing = d.rows.filter((r) => r.error).map((r) => r.ticker);
        return (
          <>
            <BarChart colorBySign x={rows.map((r) => r.ticker)} y={rows.map((r) => r[periodKey] as number)} yFormat="pct" height={300} onClick={(ev) => ev?.points?.[0] && nav(`/ticker/${encodeURIComponent(ev.points[0].x)}`)} />
            {missing.length > 0 && <div className="mk-nodata num">NO DATA · {missing.join(" ")}</div>}
          </>
        );
      }}
    </Panel>
  );
}

function CorrelationPanel({ q }: { q: OverviewQuery }) {
  const corr = q.data?.correlation_1y;
  const empty = !!q.data && (!corr || corr.tickers.length < 2);
  const labels = useMemo(() => corr?.tickers ?? [], [corr]);
  return (
    <Panel<Overview>
      notes={[]}
      title="Correlation · 1Y"
      info={{ text: "Pearson correlation of daily returns over the last year.", formula: "\\rho_{ij} = \\frac{\\operatorname{Cov}(r_i, r_j)}{\\sigma_i\\,\\sigma_j}" }}
      query={q}
      error={q.error ?? allFailed(q.data)}
      asOf={q.data?.as_of ?? undefined}
      empty={empty ? <div className="mk-nodata num">FEWER THAN 2 INSTRUMENTS WITH 1Y COMMON HISTORY</div> : undefined}
      actions={corr?.n_obs ? <span className="mk-missing num">N {corr.n_obs}</span> : undefined}
      skeletonHeight={320}
    >
      {() => <HeatmapChart x={labels} y={labels} z={corr!.matrix} diverging palette="neutral" zmin={-1} zmax={1} format="num" digits={2} showValues={labels.length <= 10} height={320} />}
    </Panel>
  );
}
