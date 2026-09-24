/**
 * Markets (/) — the REFERENCE PAGE. Read this before writing a page.
 *
 * Patterns shown here:
 *  1. Data via `useApiQuery` (GET) with typed payloads kept in a page-local module
 *     (./markets/data.ts). POST analytics use `useApiPost` the same way.
 *  2. Every data block is a <Panel query={q}> — loading skeleton, 503 "data source
 *     unavailable", other errors, `notes` and `provenance` are all handled by Panel.
 *  3. Page-level controls live in <Page actions>, and state that should survive a reload
 *     or be shareable lives in the URL (`useTabParam`).
 *  4. Numbers are formatted only through lib/format and rendered in `.num` (mono,
 *     tabular). Signs colour via `signClass` / DataTable `color: "sign"`.
 *  5. Every metric has an info tooltip (glossary key or inline {text, formula}).
 *  6. Page-local components & CSS live in ./markets/ (prefix classes, e.g. `mk-`).
 */
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { BarChart, HeatmapChart, Page, Panel, Section, SegmentedControl, Skeleton, StartHere, useTabParam } from "../components";
import { fmtDate } from "../lib/format";
import { prettyName, useUniverses } from "../lib/market";
import { useApiQuery } from "../lib/query";
import { IndexCard } from "./markets/IndexStrip";
import { RatesRow, RegimeStrip } from "./markets/MacroStrip";
import { MarketTable } from "./markets/MarketTable";
import { SectorHeatmap } from "./markets/SectorHeatmap";
import { PERIODS, allFailed, periodOf, useOverview, type Overview } from "./markets/data";
import "./markets/markets.css";

/** Universe keys given dedicated treatment; everything else becomes a table. */
const HERO = "us_equity_indices";
const SECTORS = "sectors";
const VOL = "volatility";
/** A small cross-asset basket for the correlation matrix and the period bar chart. */
const CROSS_ASSET = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG", "GLD", "USO", "BTC-USD"];

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
  const asOf = [hero.data?.as_of, sectors.data?.as_of, cross.data?.as_of].filter(Boolean).sort().pop();

  return (
    <Page
      eyebrow={asOf ? `Closing prices · ${fmtDate(asOf)}` : "Markets"}
      title="Markets"
      subtitle="Where every major asset class stands today, and how it got here. Click any instrument for its full history."
      meta={
        health.data?.offline ? (
          <span className="badge unknown" title="The backend is running in offline mode: only committed daily data is available.">
            Offline dataset — last committed closes
          </span>
        ) : undefined
      }
      actions={
        <SegmentedControl
          ariaLabel="Return horizon"
          options={PERIODS.map((p) => ({ value: p.label, label: p.label, title: `${p.long} return` }))}
          value={period.label}
          onChange={setPeriod}
        />
      }
    >
      {/* 0 — first-visit orientation (dismissible, remembered in localStorage) */}
      <StartHere />

      {/* 1 — headline indices + VIX */}
      <HeroStrip hero={hero} vix={vix} periodKey={period.key} periodLabel={period.label} />

      {/* 1b — macro regime (Markov switching on SPY, weekly) + the Treasury curve */}
      <RegimeStrip />
      <Section title="Rates" description="The Treasury curve today against a month and a year ago, and the 2-year / 10-year yields that anchor it.">
        <RatesRow />
      </Section>

      {/* 2 — sectors */}
      <Section title="Sectors" description={`S&P 500 sectors by ${period.long} return, strongest first. Colour intensity is scaled to ±${Math.round(period.scale * 100)}% for this horizon.`}>
        <div className="grid-3">
          <Panel<Overview> title="Sector heatmap" info={{ text: "Each tile is a Select Sector SPDR ETF; green tiles rose over the chosen horizon, vermilion tiles fell. Greyed tiles have no data from any configured source." }} query={sectors} error={sectors.error ?? allFailed(sectors.data)} span={2} skeletonHeight={300}>
            {(d) => <SectorHeatmap rows={d.rows} period={period.key} scale={period.scale} />}
          </Panel>
          <Panel<Overview> notes={[]} title={`${period.label} leaders & laggards`} info={{ text: "Sector returns over the chosen horizon, as bars. Hover for the exact value." }} query={sectors} error={sectors.error ?? allFailed(sectors.data)} skeletonHeight={300}>
            {(d) => {
              const rows = d.rows.filter((r) => !r.error && r[period.key] != null).sort((a, b) => (b[period.key] as number) - (a[period.key] as number));
              return <BarChart horizontal colorBySign x={rows.map((r) => r.name)} y={rows.map((r) => r[period.key] as number)} yFormat="pct" height={Math.max(180, rows.length * 34 + 40)} layout={{ margin: { l: 8, r: 16, t: 8, b: 28 } }} />;
            }}
          </Panel>
        </div>
        <Panel<Overview> notes={[]} title="Sector detail" query={sectors} error={sectors.error ?? allFailed(sectors.data)} flush skeletonHeight={240}>
          {(d) => <MarketTable rows={d.rows} />}
        </Panel>
      </Section>

      {/* 3 — every other universe as a table */}
      <Section title="Across assets" description="Styles, rates, credit, international equity, commodities and crypto. Returns use each source's adjusted closes (the panel notes say whether dividends are included) over calendar look-backs.">
        {universes.isLoading && <Skeleton height={240} />}
        <div className="mk-groups">
          {universes.isError && <Panel title="Universes" error={universes.error} onRetry={() => universes.refetch()} />}
          {groups.map((u) => (
            <GroupPanel key={u.name} name={u.name} label={u.label || prettyName(u.name)} description={u.description} />
          ))}
        </div>
      </Section>

      {/* 4 — cross-asset relationships */}
      <Section title="How assets move together" description="A basket spanning equities, bonds, credit, gold, oil and bitcoin.">
        <div className="grid-2">
          <CrossAssetBars q={cross} periodKey={period.key} periodLabel={period.label} />
          <CorrelationPanel q={cross} />
        </div>
      </Section>
    </Page>
  );
}

function HeroStrip({ hero, vix, periodKey, periodLabel }: { hero: ReturnType<typeof useOverview>; vix: ReturnType<typeof useOverview>; periodKey: (typeof PERIODS)[number]["key"]; periodLabel: string }) {
  if (hero.isLoading)
    return (
      <div className="mk-strip">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} height={112} />
        ))}
      </div>
    );
  if (hero.isError) return <Panel title="US equity indices" error={hero.error} onRetry={() => hero.refetch()} />;
  const rows = [...(hero.data?.rows ?? []), ...(vix.data?.rows ?? [])];
  return (
    <div className="mk-strip">
      {rows.map((r) => (
        <IndexCard key={r.ticker} row={r} period={periodKey} periodLabel={periodLabel} level={r.ticker.startsWith("^")} />
      ))}
    </div>
  );
}

function GroupPanel({ name, label, description }: { name: string; label: string; description?: string }) {
  const q = useOverview(name);
  const failed = allFailed(q.data);
  const ok = q.data?.rows.filter((r) => !r.error).length ?? 0;
  return (
    <Panel<Overview>
      title={label}
      subtitle={description}
      query={q}
      error={q.error ?? failed}
      compact
      flush
      skeletonHeight={160}
      actions={q.data && !failed && ok < q.data.rows.length ? <span className="badge unknown">{q.data.rows.length - ok} of {q.data.rows.length} unavailable</span> : undefined}
    >
      {(d) => <MarketTable rows={d.rows} />}
    </Panel>
  );
}

function CrossAssetBars({ q, periodKey, periodLabel }: { q: ReturnType<typeof useOverview>; periodKey: (typeof PERIODS)[number]["key"]; periodLabel: string }) {
  const nav = useNavigate();
  return (
    <Panel<Overview> title={`${periodLabel} return by asset`} info={{ text: "Total return (adjusted close) over the chosen horizon for each instrument in the basket. Click a bar to open the ticker." }} query={q} error={q.error ?? allFailed(q.data)} skeletonHeight={320}>
      {(d) => {
        const rows = d.rows.filter((r) => !r.error && r[periodKey] != null).sort((a, b) => (b[periodKey] as number) - (a[periodKey] as number));
        const missing = d.rows.filter((r) => r.error).map((r) => r.ticker);
        return (
          <>
            <BarChart colorBySign x={rows.map((r) => r.ticker)} y={rows.map((r) => r[periodKey] as number)} yFormat="pct" height={300} onClick={(ev) => ev?.points?.[0] && nav(`/ticker/${encodeURIComponent(ev.points[0].x)}`)} />
            {missing.length > 0 && <div className="subtle small" style={{ marginTop: 8 }}>No data for {missing.join(", ")}.</div>}
          </>
        );
      }}
    </Panel>
  );
}

function CorrelationPanel({ q }: { q: ReturnType<typeof useOverview> }) {
  const corr = q.data?.correlation_1y;
  const empty = !!q.data && (!corr || corr.tickers.length < 2);
  const labels = useMemo(() => corr?.tickers ?? [], [corr]);
  return (
    <Panel<Overview>
      notes={[]}
      title="1-year correlation"
      info={{ text: "Pearson correlation of daily returns over the last year: +1 means two assets move in lockstep, 0 unrelated, −1 opposite. Diversification comes from low or negative correlations.", formula: "\\rho_{ij} = \\frac{\\operatorname{Cov}(r_i, r_j)}{\\sigma_i\\,\\sigma_j}" }}
      query={q}
      error={q.error ?? allFailed(q.data)}
      empty={empty ? <div className="subtle small">Fewer than two instruments have a year of common history.</div> : undefined}
      subtitle={corr?.n_obs ? `${corr.n_obs} common sessions` : undefined}
      skeletonHeight={320}
    >
      {() => <HeatmapChart x={labels} y={labels} z={corr!.matrix} diverging palette="neutral" zmin={-1} zmax={1} format="num" digits={2} showValues={labels.length <= 10} height={320} />}
    </Panel>
  );
}
