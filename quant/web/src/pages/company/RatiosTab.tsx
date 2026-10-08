/**
 * RATIOS — GET /api/fundamentals/{t}/ratios: the latest ratios, their history (fiscal years or
 * rolling TTM) on uPlot lines with direct labels, growth vs a year earlier and the CAGRs as
 * ruled tables, and ROE's DuPont split as one ruled equation.
 */
import { useMemo } from "react";
import { XYChart, type XYSeries } from "../../charts/XYChart";
import { DataTable, Panel, SegmentedControl, StatGrid, StatTile, useTabParam, type Column } from "../../components";
import { Note } from "../../design";
import type { ValueFormat } from "../../charts/scales";
import { fmtDate, fmtPct, fmtSignedPct } from "../../lib/format";
import { INFO } from "./info";
import { Controls, Ctl, mult, periodLabel, useRatios } from "./shared";
import type { Ratios } from "./types";

type Basis = "annual" | "ttm";

/** Line styles that stay legible in ink only: tone × dash. */
const STYLE: Pick<XYSeries, "tone" | "dash">[] = [
  { tone: "ink", dash: "solid" },
  { tone: "ink", dash: "dash" },
  { tone: "ink2", dash: "solid" },
  { tone: "ink2", dash: "dot" },
  { tone: "ink3", dash: "solid" },
];

export const LABEL: Record<string, string> = {
  gross_margin: "GROSS",
  operating_margin: "OPER",
  ebitda_margin: "EBITDA",
  net_margin: "NET",
  fcf_margin: "FCF",
  roe: "ROE",
  roa: "ROA",
  roic: "ROIC",
  debt_to_equity: "D/E",
  net_debt_to_ebitda: "ND/EBITDA",
  liabilities_to_assets: "L/A",
  interest_coverage: "COVER",
  current_ratio: "CURRENT",
  quick_ratio: "QUICK",
  cash_ratio: "CASH",
  dso_days: "DSO",
  dio_days: "DIO",
};

/** Series for the keys with any data, styled in order. */
export function ratioSeries(frame: Ratios["annual"] | null, keys: string[]): XYSeries[] {
  if (!frame) return [];
  return keys
    .filter((k) => ((frame.data[k] ?? []) as (number | null)[]).some((v) => v != null))
    .map((k, i) => ({ name: LABEL[k] ?? k.toUpperCase(), y: (frame.data[k] ?? []) as (number | null)[], ...STYLE[i % STYLE.length] }));
}

const day = (d: string | null | undefined) => fmtDate(d, "short-year").toUpperCase();

export function RatiosTab({ ticker }: { ticker: string }) {
  const q = useRatios(ticker);
  const [basis, setBasis] = useTabParam<Basis>("basis", "annual");
  const ttmEmpty = !!q.data && !q.data.ttm.index.length;
  const useTtm = basis === "ttm" && !ttmEmpty;
  const frame = q.data ? (useTtm ? q.data.ttm : q.data.annual) : null;
  const x = useMemo(() => (frame ? (frame.index as string[]) : []), [frame]);
  const S = useMemo(
    () => ({
      margins: ratioSeries(frame, ["gross_margin", "operating_margin", "ebitda_margin", "net_margin", "fcf_margin"]),
      returns: ratioSeries(frame, ["roe", "roa", "roic"]),
      leverage: ratioSeries(frame, ["debt_to_equity", "net_debt_to_ebitda", "liabilities_to_assets"]),
      coverage: ratioSeries(frame, ["interest_coverage"]),
      liquidity: ratioSeries(frame, ["current_ratio", "quick_ratio", "cash_ratio"]),
      efficiency: ratioSeries(frame, ["dso_days", "dio_days"]),
    }),
    [frame],
  );
  const tag = useTtm ? "TTM" : "FY";
  const asOf = x.at(-1);

  const chart = (title: string, series: XYSeries[], fmt: ValueFormat, digits: number, note?: { n: number; to: string }, zero = true) => (
    <Panel<Ratios>
      title={
        <>
          {title} · {tag}
          {note && <Note n={note.n} to={note.to} />}
        </>
      }
      query={q}
      notes={[]}
      provenance={[]}
      skeletonHeight={240}
      asOf={asOf}
    >
      {() => (series.length && x.length > 1 ? <XYChart time x={x} series={series} yFormat={fmt} digits={digits} height={240} zero={zero} ariaLabel={`${title} history`} /> : <div className="co-none">NOT ENOUGH PERIODS</div>)}
    </Panel>
  );

  return (
    <div className="stack">
      <Controls right={q.data ? <>LATEST {q.data.latest.basis === "ttm" ? "TTM" : "FY"} · {day(q.data.latest.period_end)}</> : undefined}>
        <Ctl label="BASIS">
          <SegmentedControl
            size="sm"
            ariaLabel="Ratio basis"
            options={[
              { value: "annual", label: "FY" },
              { value: "ttm", label: "TTM", disabled: ttmEmpty, title: ttmEmpty ? "No run of four consecutive quarters in the filings" : "One point per quarter end, each a trailing-twelve-month window" },
            ]}
            value={basis}
            onChange={setBasis}
          />
        </Ctl>
      </Controls>

      <Panel<Ratios>
        title={
          <>
            LATEST · {q.data?.latest.basis === "ttm" ? "TTM" : "FY"}
            <Note n={1} to="ratios" />
          </>
        }
        query={q}
        notes={[]}
        provenance={[]}
        skeletonHeight={120}
        asOf={q.data?.latest.period_end}
      >
        {(d) => {
          const v = d.latest.values;
          const tone = (x: number | null | undefined) => (x != null && x < 0 ? "loss" : "neutral");
          return (
            <StatGrid min={120}>
              <StatTile size="sm" label="GROSS MGN" value={v.gross_margin} format={(x) => fmtPct(x, 1)} info={INFO.gross_margin} />
              <StatTile size="sm" label="OPER MGN" value={v.operating_margin} format={(x) => fmtPct(x, 1)} tone={tone(v.operating_margin)} info={INFO.operating_margin} />
              <StatTile size="sm" label="NET MGN" value={v.net_margin} format={(x) => fmtPct(x, 1)} tone={tone(v.net_margin)} info={INFO.net_margin} />
              <StatTile size="sm" label="ROE" value={v.roe} format={(x) => fmtPct(x, 1)} tone={tone(v.roe)} info={INFO.roe} />
              <StatTile size="sm" label="ROIC" value={v.roic} format={(x) => fmtPct(x, 1)} tone={tone(v.roic)} info={INFO.roic} />
              <StatTile size="sm" label="D / E" value={mult(v.debt_to_equity, 2)} info={INFO.debt_to_equity} />
              <StatTile size="sm" label="ND / EBITDA" value={mult(v.net_debt_to_ebitda, 2)} info={INFO.net_debt_to_ebitda} />
              <StatTile size="sm" label="INT COVER" value={mult(v.interest_coverage, 1)} info={INFO.interest_coverage} />
              <StatTile size="sm" label="CURRENT" value={mult(v.current_ratio, 2)} info={INFO.current_ratio} />
            </StatGrid>
          );
        }}
      </Panel>

      <div className="grid-2">
        {chart("MARGINS", S.margins, "pct", 1)}
        {chart("RETURNS ON CAPITAL", S.returns, "pct", 1)}
        {chart("LEVERAGE", S.leverage, "x", 2)}
        {chart("INTEREST COVER", S.coverage, "x", 1)}
        {chart("LIQUIDITY", S.liquidity, "x", 2)}
        {chart("WORKING CAPITAL · DAYS", S.efficiency, "num", 0)}
      </div>

      <Panel<Ratios>
        title={
          <>
            DUPONT · ROE · {q.data?.latest.basis === "ttm" ? "TTM" : "FY"}
            <Note n={2} to="ratios" />
          </>
        }
        query={q}
        notes={[]}
        provenance={[]}
        skeletonHeight={80}
        asOf={q.data?.latest.period_end}
      >
        {(d) => <DuPont values={d.latest.values} />}
      </Panel>

      <div className="grid-2">
        <Panel<Ratios> title={`GROWTH · YOY · ${tag}`} query={q} flush notes={[]} provenance={[]} skeletonHeight={200} asOf={asOf}>
          {() => frame && <GrowthTable frame={frame} basis={useTtm ? "ttm" : "annual"} />}
        </Panel>
        <Panel<Ratios> title="CAGR · TO LATEST FY" query={q} flush skeletonHeight={160} asOf={q.data?.annual.index.at(-1) as string | undefined}>
          {(d) => <CagrTable data={d} />}
        </Panel>
      </div>
    </div>
  );
}

function DuPont({ values: v }: { values: Record<string, number | null> }) {
  const m = v.dupont_net_margin;
  const t = v.dupont_asset_turnover;
  const l = v.dupont_equity_multiplier;
  const roe = m != null && t != null && l != null ? m * t * l : v.roe;
  return (
    <div className="co-dupont num">
      <DuTerm label="NET MGN" value={fmtPct(m, 1)} />
      <span className="co-dupont-op">×</span>
      <DuTerm label="ASSET TURN" value={mult(t, 2)} />
      <span className="co-dupont-op">×</span>
      <DuTerm label="EQUITY MULT" value={mult(l, 2)} />
      <span className="co-dupont-op">=</span>
      <DuTerm label="ROE" value={fmtPct(roe, 1)} strong tone={roe != null && roe < 0 ? "loss" : ""} />
    </div>
  );
}

function DuTerm({ label, value, strong, tone }: { label: string; value: string; strong?: boolean; tone?: string }) {
  return (
    <div className={`co-dupont-term ${strong ? "strong" : ""}`}>
      <div className="co-ctl-k">{label}</div>
      <div className={`co-dupont-val ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

interface GrowthRow {
  period: string;
  revenue_growth: number | null;
  eps_growth: number | null;
  fcf_growth: number | null;
  net_income_growth: number | null;
}

function GrowthTable({ frame, basis }: { frame: Ratios["annual"]; basis: Basis }) {
  const rows: GrowthRow[] = (frame.index as string[])
    .map((p, i) => {
      const c = (k: string) => ((frame.data[k] ?? []) as (number | null)[])[i] ?? null;
      return { period: p, revenue_growth: c("revenue_growth"), eps_growth: c("eps_growth"), fcf_growth: c("fcf_growth"), net_income_growth: c("net_income_growth") };
    })
    .filter((r) => r.revenue_growth != null || r.eps_growth != null || r.fcf_growth != null || r.net_income_growth != null)
    .reverse();
  if (!rows.length) return <div className="co-none co-pad">NO GROWTH ROWS</div>;
  const col = (key: keyof GrowthRow, label: string): Column<GrowthRow> => ({ key, label, numeric: true, sortable: false, format: (x: number | null) => fmtSignedPct(x, 1), color: "sign" });
  const cols: Column<GrowthRow>[] = [
    { key: "period", label: "PERIOD", sortable: false, render: (r) => <span className="num">{periodLabel(r.period, basis === "ttm" ? "ttm" : "annual")}</span> },
    col("revenue_growth", "REV"),
    col("eps_growth", "EPS"),
    col("fcf_growth", "FCF"),
    col("net_income_growth", "NI"),
  ];
  return <DataTable columns={cols} rows={rows} rowKey={(r) => r.period} />;
}

interface CagrRow {
  item: string;
  "1y": number | null;
  "3y": number | null;
  "5y": number | null;
}
const CAGR_LABEL: Record<string, string> = { revenue: "REVENUE", eps_diluted: "EPS DIL", fcf: "FCF", net_income: "NET INCOME" };

function CagrTable({ data }: { data: Ratios }) {
  const rows: CagrRow[] = Object.entries(data.growth_cagr).map(([item, r]) => ({ item, "1y": r["1y"] ?? null, "3y": r["3y"] ?? null, "5y": r["5y"] ?? null }));
  const cols: Column<CagrRow>[] = [
    { key: "item", label: "LINE", render: (r) => CAGR_LABEL[r.item] ?? r.item.toUpperCase(), sortable: false, info: INFO.cagr },
    ...(["1y", "3y", "5y"] as const).map((h) => ({ key: h, label: h.toUpperCase(), numeric: true, sortable: false, format: (x: number | null) => fmtSignedPct(x, 1), color: "sign" as const })),
  ];
  return <DataTable columns={cols} rows={rows} rowKey={(r) => r.item} />;
}

