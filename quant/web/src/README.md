# OhCamel Quant web — guide for page authors

Vite + React 18 + TypeScript (strict). `npm run dev` (proxy `/api` → `:8090`), `npm run build`
(→ `web/dist`, served by FastAPI), `npm run typecheck`, `npm run lint`,
`npm run screenshots` (Playwright against a running backend).

**Read `pages/Markets.tsx` first.** It is the reference page, and its header comment lists the
patterns every page follows.

## Where your code goes

| You own | Path |
|---|---|
| Your page | `src/pages/<Page>.tsx` (default export; already routed in `lib/routes.ts`) |
| Page-local components, types, CSS | `src/pages/<page>/…`, with CSS classes prefixed per page (e.g. `mk-`, `tk-`) |

Don't edit shared files (`components/`, `lib/`, `styles/`) for a single page's needs. If a
shared component is missing something, add an optional prop without breaking existing callers.
Remove the `<PagePlaceholder/>` when you build a page.

## Data

```ts
import { useApiQuery, useApiPost } from "../lib/query";
import { usePortfolio } from "../lib/portfolio";

const q = useApiQuery<Overview>("/market/overview", { universe });            // GET
const { request } = usePortfolio();                                           // PortfolioIn body
const r = useApiPost<VarOut>("/risk/var", { ...request, alpha: 0.99 });       // POST, cached by body
```

- Paths are relative to `/api`. Params that are `undefined`, `null` or `""` are dropped.
- Errors are `ApiError` (`.status`, `.detail` = the server's `detail`). **503 is
  `DataUnavailableError`**: render it through `<Panel>`/`<ErrorState>`, never as a blank or
  fabricated chart.
- `staleTime` is 5 min for GETs and 30 min for POSTs. 4xx and 503 responses are never retried.
  Previous data stays visible while new params load; pass `placeholderData: undefined` to
  turn that off (for example when the ticker changes).
- Wire shapes (`lib/types.ts`): series `{index, values}`, frame `{index, columns, data:{col:[…]}}`,
  records `[{…}]`. Convert them with `lib/series.ts`: `seriesXY`, `frameColumn`,
  `frameToTraces`, `frameToRows`, `frameToMatrix`, `rebase`, `cumulative`, `drawdown`,
  `pctChange`, `isSeries`, `isFrame`.
- Units: returns, weights and vols are **decimals**. FRED yields are **percent**. Use `fmtPct`
  for decimals and `fmtPctPoints` for percent values.
- `lib/market.ts` has tolerant hooks: `useTickerSearch`, `useUniverses`, `use13FFilers`,
  `use13F`.
- `lib/stats.ts` has descriptive transforms of data already on screen: `rollingVol`,
  `pathStats`, `monthlyReturns`, `quantile`. Real analytics belong in the backend. When you
  compute something in the browser, say so in the panel's `notes`.

## Components (`import { … } from "../components"`)

| Component | Use |
|---|---|
| `Page` `{title, eyebrow?, subtitle?, actions?, meta?, docTitle?}` | Page frame with the display-serif title. It also sets `document.title`. |
| `Section` `{title, description?, actions?}` | Titled group of panels. `actions` wrap onto more lines on narrow screens (they never widen the page). Long `.badge`s in `Page meta` wrap too. |
| `Panel` / `Card` `{title, subtitle?, info?, actions?, query?, loading?, error?, empty?, applicable?, provenance?, notes?, flush?, compact?, span?, skeletonHeight?}` | The data container. Pass `query={q}` and a render function `{(data) => …}`. It handles the loading skeleton, the 503 "data source unavailable" state, other errors, `notes` (collapsible) and `provenance` (read from the payload automatically). `flush` is for edge-to-edge tables. `span={2 \| "all"}` works inside `.grid-*`. Precedence: `applicable={false}` (shows `empty` or “Not applicable here”, hides errors/notes/provenance) → `empty` → error → loading → content, so a panel that has nothing to show never surfaces an unrelated error. |
| `StatGrid` `{min?}` + `StatTile` `{label, value, format?, delta?, deltaFormat?, deltaLabel?, invert?, tone?, info?, caption?, loading?, size?}` | Headline numbers. `tone="auto"` colours the value by its sign. `size="sm" \| "md" \| "lg"`; values are never ellipsized — inside a `StatGrid` they shrink with the tile and wrap as a last resort. |
| `DataTable` `{columns, rows, rowKey?, onRowClick?, defaultSort?, maxHeight?, isActive?}` | Sortable table with a sticky header. Column options: `{key, label, numeric?, format?, render?, value?, color?: "sign" \| fn, heat?: {min,max}, info?, hideBelow?: 600\|900\|1200\|1440\|1600, wrap?}`. `wrap` lets long text (references) wrap. |
| `Tabs` + `useTabParam(key, default)` | Underlined tabs whose state lives in the URL (`?tab=`). |
| `SegmentedControl`, `Select`, `Toggle`, `Field` | Small controls. All are controlled components. |
| `NumberField` `{value, onChange, unit?, percent?, min?, max?, step?}` | `percent` edits a decimal as a percent (0.05 ⇄ “5 %”). |
| `Slider` `{value, min, max, step?, format?}` | Range slider with a live readout. |
| `DateRangePicker` `{value:{start,end}, onChange, anchor?}` | Presets for 1Y / 3Y / 5Y / 10Y / Max, plus two date inputs. |
| `ParamForm` + `paramDefaults(params)` | Renders `[{name,type,default,min,max,description,choices?,unit?}]` into controls. |
| `TickerInput` `{onSelect}` / `TickerChips` `{tickers, onChange?, addable?, colored?}` | Ticker entry with autocomplete. |
| `PortfolioBuilder` `{value?, onChange?, showSettings?, showSources?}` | Edits the global portfolio by default: weights, equal-weight, normalize, presets, 13F import (top N), CSV paste, benchmark, notional, window. |
| `TimeSeriesChart` `{series:[{name,x,y,color?,dash?,fill?}], yFormat, rangeSelector?, logY?, baseline?, area?}` | Line and area charts. With `rangeSelector` the legend sits below the plot. `yFormat="usd"` ticks never use SI “m” (milli) — `$0.8`, `$1,200`. |
| `BarChart` `{x,y \| series, yFormat, horizontal?, colorBySign?, barmode?}` | Bar charts. (Safe without `colorBySign` for single and multi-series.) |
| `HeatmapChart` `{x,y,z, format, diverging?, palette?: "pnl"\|"neutral", showValues?, zmin?, zmax?, gap?}` | Heatmaps. `gap` = px between cells (default 2; 0 for dense time × asset grids). |
| `SurfaceChart` `{x,y,z, zFormat, titles}` | 3-D surfaces. |
| `HistogramChart` `{values, format, vlines?}` | Histograms. |
| `Chart` `{data \| (tokens)=>data, layout \| (tokens)=>layout, height}` | Raw Plotly with the theme applied. Colours may be `"var(--gain)"`, or you can build traces from `tokens` so they follow the theme toggle. Helpers: `withAlpha`, `resolveColor`, `mergeLayout`, `d3Format`. |
| `Sparkline` `{values}` | Tiny SVG line, no Plotly. Use it in tables. |
| `Formula` `{tex, inline?}` / `InfoTip` `{info}` | KaTeX and the “?” popover. `info` is a glossary key (`lib/glossary.ts`: `sharpe`, `sortino`, `vol`, `var`, `es`/`expected_shortfall`, `beta`, `max_drawdown`, `cagr`, `risk_contribution`, `effective_bets`, `diversification_ratio`, `shrinkage`, `condition_number`, `marchenko_pastur`, `psr`, `dsr`, `pbo`, …) or `{title?, text, formula?, reference?, href?}`. |
| `Provenance`, `EmptyState`, `ErrorState`, `Skeleton`, `Callout`, `Notes`, `Icon` | Building blocks. |
| `StartHere` | The dismissible first-visit guide on Markets (dismissal kept in localStorage). |

Formatters (`lib/format.ts`): `fmtNum`, `fmtPct`, `fmtSignedPct`, `fmtPctPoints`, `fmtBps`,
`fmtCurrency({compact})`, `fmtCompact`, `fmtMultiple`, `fmtAuto`, `fmtDate(style)` (`"medium"` 11 Feb 2026, `"short"` 11 Feb, `"short-year"` 11 Feb 26, `"month"`, `"year"`, `"iso"`),
`fmtRelativeTime`, `parseDate`, `toIsoDate`, `signClass`. All of them return “—” for
null, undefined or NaN.

## Design rules

- Colours come from tokens only (`styles/tokens.css`). Never write a raw hex in a page. The
  semantic colours are `--gain`, `--loss`, `--warn` and `--unknown` (lilac = unavailable).
- Series take `--c1…--c8` in fixed order, and Chart does this automatically. The order is
  validated for colour-vision deficiency, so don't reorder it or cycle past 8: series 9+ get
  the neutral `--c-other`. Use `seriesVar(i)` (CSS) / `seriesColor(tokens, i)` (Plotly) from
  `lib/theme` instead of `var(--c${i % 8 + 1})`. Better still, fold extras into “Other” or use
  small multiples.
- Never draw two y-axes. Use two charts, or index both series to a common base.
- Every number goes in `.num` (mono, tabular figures). Titles use the display serif (`Page`,
  `Section`).
- Every metric gets an `info`: one plain-English sentence, plus a formula and reference when
  relevant.
- Mobile: `.grid-2/3/4` collapse on their own. Use `hideBelow` on low-priority table columns.
- Utility classes (`styles/global.css`): `.stack .row .row-wrap .grid-2 .grid-3 .grid-4
  .grid-auto .span-2 .span-all .num .muted .subtle .small .gain .loss .eyebrow .btn
  .btn-primary .btn-ghost .btn-sm .icon-btn .input .select .kbd .badge(.gain/.loss/.warn/.unknown/.accent)`.

## Routes & navigation

`lib/routes.ts` is the single registry (router, sidebar, command palette); its order is the
sidebar order: Markets · Portfolio Lab, Optimizer, Strategy Lab · Volatility, Rates & Macro,
Company · Live Engine, Methodology. `/company` (no ticker) renders the Company landing.
The Optimizer accepts `/optimize?tickers=A,B&weights=0.4,0.6&start=…&end=…&from=portfolio`.

## Portfolio context

`usePortfolio()` returns `{portfolio, request, setPortfolio, update, setHoldings, reset,
shareUrl}`. The portfolio is saved to localStorage, and `shareUrl()` builds a `?p=` link that
the app adopts when opened. Pure helpers: `normalizeWeights`, `equalWeight`, `consolidate`,
`parseHoldingsCsv`, `totalWeight`, `grossWeight`, `encodePortfolio`, `decodePortfolio`,
`toRequest`.
