/**
 * Ratio history: margins, returns on capital, leverage & liquidity, efficiency, growth,
 * a DuPont breakdown of ROE and a CAGR table. Annual (fiscal years) or rolling TTM.
 */
import { useMemo } from "react";
import { Chart, DataTable, Panel, SegmentedControl, StatGrid, StatTile, TimeSeriesChart, useTabParam, type Column } from "../../components";
import { fmtDate, fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import { INFO } from "./info";
import { mult, periodLabel, useRatios } from "./shared";
import type { Ratios } from "./types";
import type { Data } from "plotly.js";
import type { Tokens } from "../../lib/theme";

type Basis = "annual" | "ttm";

const LABEL: Record<string, string> = {
  gross_margin: "Gross",
  operating_margin: "Operating",
  ebitda_margin: "EBITDA",
  net_margin: "Net",
  fcf_margin: "FCF",
  roe: "ROE",
  roa: "ROA",
  roic: "ROIC",
  debt_to_equity: "Debt / equity",
  net_debt_to_ebitda: "Net debt / EBITDA",
  liabilities_to_assets: "Liabilities / assets",
  interest_coverage: "Interest coverage",
  current_ratio: "Current",
  quick_ratio: "Quick",
  cash_ratio: "Cash",
  dso_days: "Days sales outstanding",
  dio_days: "Days inventory outstanding",
  revenue_growth: "Revenue",
  eps_growth: "EPS",
  fcf_growth: "Free cash flow",
  net_income_growth: "Net income",
};

export function RatiosTab({ ticker, compact }: { ticker: string; compact?: boolean }) {
  const q = useRatios(ticker);
  const [basis, setBasis] = useTabParam<Basis>("basis", "annual");
  const frame = q.data ? (basis === "ttm" && q.data.ttm.index.length ? q.data.ttm : q.data.annual) : null;
  const x = useMemo(() => (frame ? (frame.index as string[]) : []), [frame]);
  // Series are memoized per frame so Plotly is not re-driven on unrelated re-renders.
  const S = useMemo(() => {
    const col = (k: string) => (frame?.data[k] ?? []) as (number | null)[];
    const lines = (keys: string[]) => keys.filter((k) => col(k).some((v) => v != null)).map((k) => ({ name: LABEL[k] ?? k, x, y: col(k) }));
    return {
      margins: lines(["gross_margin", "operating_margin", "ebitda_margin", "net_margin", "fcf_margin"]),
      returns: lines(["roe", "roa", "roic"]),
      leverage: lines(["debt_to_equity", "net_debt_to_ebitda", "liabilities_to_assets"]),
      coverage: lines(["interest_coverage"]),
      liquidity: lines(["current_ratio", "quick_ratio", "cash_ratio"]),
      efficiency: lines(["dso_days", "dio_days"]),
      growth: ["revenue_growth", "eps_growth", "fcf_growth"].map((k) => ({ name: LABEL[k], x: x.map((p) => periodLabel(p, basis === "ttm" ? "ttm" : "annual")), y: col(k) })),
    };
  }, [frame, x, basis]);
  const ttmEmpty = !!q.data && !q.data.ttm.index.length;

  const basisCtl = (
    <SegmentedControl
      size="sm"
      ariaLabel="Ratio basis"
      options={[
        { value: "annual", label: "Fiscal years" },
        { value: "ttm", label: "Rolling TTM", disabled: ttmEmpty, title: ttmEmpty ? "No run of four consecutive quarters in the filings" : "One point per quarter end, each a trailing-twelve-month window" },
      ]}
      value={basis}
      onChange={setBasis}
    />
  );

  if (q.isError || q.isLoading || !q.data) return <Panel title="Ratios" subtitle="Margins, returns, leverage, efficiency and growth over time." query={q} compact={compact} actions={basisCtl} skeletonHeight={420} />;
  const d = q.data;
  const v = d.latest.values;

  return (
    <div className="stack-lg">
      <Panel<Ratios>
        title="Where the business stands now"
        subtitle={`Latest ${d.latest.basis === "ttm" ? "trailing-twelve-month" : "fiscal-year"} ratios, period ending ${fmtDate(d.latest.period_end)}. Hover any figure for its definition.`}
        query={q}
        notes={[]}
        provenance={[]}
        actions={basisCtl}
        skeletonHeight={120}
      >
        {() => (
          <StatGrid min={150}>
            <StatTile label="Gross margin" value={v.gross_margin} format={(x) => fmtPct(x, 1)} info={INFO.gross_margin} />
            <StatTile label="Operating margin" value={v.operating_margin} format={(x) => fmtPct(x, 1)} tone="auto" info={INFO.operating_margin} />
            <StatTile label="Net margin" value={v.net_margin} format={(x) => fmtPct(x, 1)} tone="auto" info={INFO.net_margin} />
            <StatTile label="ROE" value={v.roe} format={(x) => fmtPct(x, 1)} tone="auto" info={INFO.roe} />
            <StatTile label="ROIC" value={v.roic} format={(x) => fmtPct(x, 1)} tone="auto" info={INFO.roic} />
            <StatTile label="Debt / equity" value={mult(v.debt_to_equity, 2)} info={INFO.debt_to_equity} />
            <StatTile label="Net debt / EBITDA" value={mult(v.net_debt_to_ebitda, 2)} info={INFO.net_debt_to_ebitda} />
            <StatTile label="Interest cover" value={mult(v.interest_coverage, 1)} info={INFO.interest_coverage} />
            <StatTile label="Current ratio" value={mult(v.current_ratio, 2)} info={INFO.current_ratio} />
          </StatGrid>
        )}
      </Panel>

      <div className="grid-2">
        <Panel title="Margins" subtitle="How much of each sales dollar survives each layer of cost. Rising margins usually mean pricing power or operating leverage." info={{ title: "Margins", text: "Gross → operating → net: each line subtracts another layer of cost from revenue. FCF margin is the cash version.", formula: "\\text{margin} = \\frac{\\text{profit measure}}{\\text{revenue}}" }} notes={[]}>
          <TimeSeriesChart series={S.margins} yFormat="pct" digits={1} height={280} baseline={0} />
        </Panel>
        <Panel title="Returns on capital" subtitle="Profit earned on the money invested in the business. ROIC above the cost of capital is what makes growth valuable." info={INFO.roic} notes={[]}>
          <TimeSeriesChart series={S.returns} yFormat="pct" digits={1} height={280} baseline={0} />
        </Panel>
        <Panel title="Leverage" subtitle="How heavily the balance sheet leans on borrowed money. Lower is more resilient; blank where equity or EBITDA is not positive." info={INFO.net_debt_to_ebitda} notes={[]}>
          <TimeSeriesChart series={S.leverage} yFormat="x" digits={2} height={280} baseline={0} />
        </Panel>
        <Panel title="Interest coverage & liquidity" subtitle="Can the company pay its interest and its bills? Coverage is times EBIT covers interest; the liquidity ratios compare short-term assets to short-term debts." info={INFO.interest_coverage} notes={[]}>
          <div className="co-split">
            <TimeSeriesChart series={S.coverage} yFormat="x" digits={1} height={130} showLegend />
            <TimeSeriesChart series={S.liquidity} yFormat="x" digits={2} height={150} />
          </div>
        </Panel>
        <Panel title="Growth vs a year earlier" subtitle="Year-over-year change in the lines investors watch most. Blank where the prior value was not positive." info={INFO.growth} notes={[]}>
          <GroupedBars series={S.growth} height={280} />
        </Panel>
        <Panel title="Working-capital efficiency" subtitle="How long cash is tied up in receivables and inventory. Shorter cycles free up cash." info={INFO.dso} notes={[]}>
          <TimeSeriesChart series={S.efficiency} yFormat="num" digits={0} height={280} />
        </Panel>
      </div>

      <div className="grid-3">
        <Panel title="DuPont: where ROE comes from" subtitle="Return on equity split into margin × asset turnover × leverage (latest period)." info={INFO.dupont} notes={[]} span={2}>
          <DuPont values={v} />
        </Panel>
        <Panel<Ratios> title="Compound growth" subtitle="Annualized growth to the latest fiscal year." info={INFO.cagr} query={q} flush notes={d.notes} provenance={d.provenance}>
          {() => <CagrTable data={d} />}
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
    <div className="co-dupont">
      <DuTerm label="Net margin" value={fmtPct(m, 1)} hint="profit per $ of sales" />
      <span className="co-dupont-op">×</span>
      <DuTerm label="Asset turnover" value={fmt2(t)} hint="sales per $ of assets" />
      <span className="co-dupont-op">×</span>
      <DuTerm label="Equity multiplier" value={fmt2(l)} hint="assets per $ of equity" />
      <span className="co-dupont-op">=</span>
      <DuTerm label="ROE" value={fmtPct(roe, 1)} hint="profit per $ of equity" strong />
    </div>
  );
}
const fmt2 = (x: number | null | undefined) => (x == null ? "—" : `${fmtNum(x, 2)}×`);

function DuTerm({ label, value, hint, strong }: { label: string; value: string; hint: string; strong?: boolean }) {
  return (
    <div className={`co-dupont-term ${strong ? "strong" : ""}`}>
      <div className="co-mini-label">{label}</div>
      <div className="num co-dupont-val">{value}</div>
      <div className="subtle small">{hint}</div>
    </div>
  );
}

interface CagrRow {
  item: string;
  "1y": number | null;
  "3y": number | null;
  "5y": number | null;
}
const CAGR_LABEL: Record<string, string> = { revenue: "Revenue", eps_diluted: "Diluted EPS", fcf: "Free cash flow", net_income: "Net income" };

function CagrTable({ data }: { data: Ratios }) {
  const rows: CagrRow[] = Object.entries(data.growth_cagr).map(([item, r]) => ({ item, "1y": r["1y"] ?? null, "3y": r["3y"] ?? null, "5y": r["5y"] ?? null }));
  const cols: Column<CagrRow>[] = [
    { key: "item", label: "Line", render: (r) => CAGR_LABEL[r.item] ?? r.item, sortable: false },
    ...(["1y", "3y", "5y"] as const).map((h) => ({ key: h, label: h.toUpperCase(), numeric: true, format: (x: number | null) => fmtSignedPct(x, 1), color: "sign" as const, heat: { min: -0.3, max: 0.3, diverging: true } })),
  ];
  return <DataTable columns={cols} rows={rows} rowKey={(r) => r.item} />;
}

/**
 * Grouped bars built on the raw Chart (page-local styling; the shared BarChart also works now).
 */
function GroupedBars({ series, height }: { series: { name: string; x: string[]; y: (number | null)[] }[]; height: number }) {
  const data = useMemo(
    () => (t: Tokens): Data[] =>
      series.map((s, i) => ({ type: "bar", name: s.name, x: s.x, y: s.y, marker: { color: t.categorical[i] }, hovertemplate: `<b>%{fullData.name}</b> %{x}: %{y:.1%}<extra></extra>` })) as Data[],
    [series],
  );
  const layout = useMemo(() => ({ barmode: "group", showlegend: true, hovermode: "closest", xaxis: { type: "category", showgrid: false, showspikes: false }, yaxis: { tickformat: ".0%", zeroline: true }, margin: { l: 44, r: 8, t: 30, b: 30 } }) as any, []);
  return <Chart data={data} layout={layout} height={height} />;
}
