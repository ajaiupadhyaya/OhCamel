/**
 * Dashboard — GET /api/macro/dashboard: every configured FRED indicator as a card
 * (latest, 1M/3M/1Y change, 10-year percentile, 3-year sparkline), grouped by theme.
 * Unavailable series are listed compactly per group (never drawn as empty cards).
 */
import { useMemo } from "react";
import { InfoTip, Panel, Sparkline, StatGrid, StatTile } from "../../components";
import { fmtDate, fmtNum } from "../../lib/format";
import type { UseQueryResult } from "@tanstack/react-query";
import type { ApiError } from "../../lib/api";
import { CATEGORY_BLURB, CATEGORY_LABEL, INFO, TRANSFORM_LABEL } from "./info";
import { PercentileGauge, fmtUnits, ordinal } from "./shared";
import type { Dashboard, DashboardRow, Horizon } from "./types";

const FREQ: Record<string, string> = { D: "daily", W: "weekly", M: "monthly", Q: "quarterly" };
const HORIZONS: Horizon[] = ["1M", "3M", "1Y"];
const SHORT: Record<string, string> = { DFF: "Fed funds", DGS2: "2y yield", DGS10: "10y yield", T10Y3M: "10y − 3m", PCEPILFE: "Core PCE", UNRATE: "Unemployment", BAMLH0A0HYM2: "HY spread", VIXCLS: "VIX" };

export function DashboardTab({ q, onExplore }: { q: UseQueryResult<Dashboard, ApiError>; onExplore: (id: string, transform: string) => void }) {
  return (
    <Panel<Dashboard>
      title="Macro dashboard"
      subtitle="The economy at a glance: each card is one official series from FRED, with how it has moved and how unusual today's level is versus the last ten years. Click a card to chart its full history."
      info={{ text: "Latest published value of each series, its change over 1 month / 3 months / 1 year, and its percentile within the last 10 years of observations. Values are FRED's latest vintage (revisions included)." }}
      query={q}
      skeletonHeight={520}
    >
      {(d) => <DashboardBody d={d} onExplore={onExplore} />}
    </Panel>
  );
}

function DashboardBody({ d, onExplore }: { d: Dashboard; onExplore: (id: string, transform: string) => void }) {
  const byCat = useMemo(() => {
    const m = new Map<string, DashboardRow[]>();
    for (const c of d.categories) m.set(c, []);
    for (const r of d.series) m.get(r.category)?.push(r);
    return [...m.entries()];
  }, [d]);
  const ok = d.series.filter((r) => !r.error && r.latest != null);
  const sahm = d.highlights?.sahm_rule;
  const get = (id: string) => ok.find((r) => r.id === id);
  const headline = ["DFF", "DGS10", "T10Y3M", "PCEPILFE", "UNRATE", "VIXCLS"].map(get).filter((r): r is DashboardRow => !!r);

  return (
    <div className="stack-lg">
      <div className="mc-dash-summary">
        <span className="num">{ok.length}</span> of <span className="num">{d.series.length}</span> indicators available
        {ok.length < d.series.length && <span className="badge unknown">{d.series.length - ok.length} unavailable from FRED right now</span>}
      </div>

      {(headline.length > 0 || sahm) && (
        <StatGrid min={140}>
          {headline.map((r) => (
            <StatTile
              key={r.id}
              label={SHORT[r.id] ?? r.name}
              value={fmtUnits(r.latest, r.units)}
              info={{ title: r.name, text: `FRED ${r.id}, ${FREQ[r.frequency] ?? r.frequency}, latest ${fmtDate(r.date)}; ${TRANSFORM_LABEL[r.transform] ?? r.transform}. ${ordinal(r.percentile_10y)} percentile of the last 10 years.` }}
              caption={`${fmtUnits(r.change?.["1M"], r.units, { signed: true, change: true })} in 1M`}
            />
          ))}
          {sahm && (
            <StatTile label="Sahm rule" value={`${fmtNum(sahm.value, 2)}`} tone={sahm.triggered ? "loss" : "neutral"} info={INFO.sahm} caption={sahm.triggered ? `pp · triggered (≥ ${fmtNum(sahm.threshold, 2)})` : `pp · trigger ${fmtNum(sahm.threshold, 2)}`} />
          )}
        </StatGrid>
      )}

      {byCat.map(([cat, rows]) => {
        const live = rows.filter((r) => !r.error && r.latest != null);
        const off = rows.filter((r) => r.error || r.latest == null);
        return (
          <section key={cat} className="mc-cat">
            <header className="mc-cat-head">
              <h3 className="mc-cat-title">{CATEGORY_LABEL[cat] ?? cat}</h3>
              <p className="mc-cat-blurb subtle small">{CATEGORY_BLURB[cat]}</p>
            </header>
            {live.length > 0 && (
              <div className="mc-cards">
                {live.map((r) => (
                  <IndicatorCard key={r.id} r={r} onClick={() => onExplore(r.id, r.transform)} />
                ))}
              </div>
            )}
            {off.length > 0 && (
              <div className="mc-off" role="note">
                <span className="mc-off-label">{live.length ? "Also tracked — unavailable now" : "Unavailable now"}</span>
                {off.map((r) => (
                  <span key={r.id} className="mc-off-chip" title={r.error ?? "unavailable"}>
                    <span className="num">{r.id}</span> {r.name}
                  </span>
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function IndicatorCard({ r, onClick }: { r: DashboardRow; onClick: () => void }) {
  const pw = r.percentile_window;
  const pctTitle = pw ? `${ordinal(r.percentile_10y)} percentile of ${pw.n.toLocaleString()} observations, ${fmtDate(pw.start, "month")} – ${fmtDate(pw.end, "month")} (range ${fmtUnits(r.history_min, r.units)} to ${fmtUnits(r.history_max, r.units)})` : undefined;
  return (
    <div role="button" tabIndex={0} className="mc-card" onClick={onClick} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onClick())} title={`Open ${r.id} in the explorer`}>
      <div className="mc-card-head">
        <span className="mc-card-name">{r.name}</span>
        <span className="mc-card-id num">{r.id}</span>
      </div>
      <div className="mc-card-body">
        <div>
          <div className="mc-card-value num">{fmtUnits(r.latest, r.units)}</div>
          <div className="mc-card-asof subtle">
            {r.units !== "%" && <>{r.units} · </>}
            {fmtDate(r.date)}
          </div>
        </div>
        {r.sparkline && r.sparkline.values.length > 2 && <Sparkline values={r.sparkline.values} width={104} height={34} color="var(--c1)" title={`${r.id}, last 3 years`} />}
      </div>
      <div className="mc-card-changes">
        {HORIZONS.map((h) => (
          <div key={h} className="mc-card-chg">
            <span className="mc-card-chg-h">{h}</span>
            <span className="num">{fmtUnits(r.change?.[h], r.units, { signed: true, change: true })}</span>
          </div>
        ))}
      </div>
      <div className="mc-card-pct">
        <PercentileGauge value={r.percentile_10y} title={pctTitle} />
        <span className="mc-card-pct-label num">{ordinal(r.percentile_10y)}</span>
        <span className="subtle">pct · 10y</span>
        <span onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()} role="presentation">
          <InfoTip info={INFO.percentile} size={12} />
        </span>
      </div>
    </div>
  );
}
