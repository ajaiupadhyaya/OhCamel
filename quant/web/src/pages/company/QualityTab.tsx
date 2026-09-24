/**
 * Accounting quality & distress: Piotroski F (checklist), Altman Z / Z'' (zone gauges),
 * Beneish M (eight indices), Sloan accruals, Ohlson O, and their history.
 */
import { Formula, Panel, TimeSeriesChart } from "../../components";
import { Icon } from "../../components/Icon";
import { fmtDate, fmtNum, fmtPct } from "../../lib/format";
import { INFO } from "./info";
import { ContributionBar, useScores, ZoneGauge } from "./shared";
import type { Altman, Component, PiotroskiSignal, Scores } from "./types";

const SIGNAL_TEXT: Record<string, { title: string; why: string; group: "Profitability" | "Leverage & liquidity" | "Operating efficiency" }> = {
  F_ROA: { title: "Profitable", why: "Return on beginning assets is positive.", group: "Profitability" },
  F_CFO: { title: "Cash-generative", why: "Operating cash flow is positive.", group: "Profitability" },
  F_dROA: { title: "Improving profitability", why: "ROA is higher than last year.", group: "Profitability" },
  F_ACCRUAL: { title: "Earnings backed by cash", why: "Operating cash flow exceeds net income (scaled by assets).", group: "Profitability" },
  F_dLEVER: { title: "Less leverage", why: "Long-term debt / average assets fell.", group: "Leverage & liquidity" },
  F_dLIQUID: { title: "More liquid", why: "The current ratio rose.", group: "Leverage & liquidity" },
  F_EQ_OFFER: { title: "No dilution", why: "Diluted share count did not rise (proxy for no equity issuance).", group: "Leverage & liquidity" },
  F_dMARGIN: { title: "Wider gross margin", why: "Gross margin rose versus last year.", group: "Operating efficiency" },
  F_dTURN: { title: "Sweating assets harder", why: "Sales / beginning assets rose.", group: "Operating efficiency" },
};

const INPUT_LABEL: Record<string, string> = {
  roa_t: "ROA",
  "roa_t-1": "ROA prior",
  cfo_to_assets_t: "CFO / assets",
  lever_t: "LTD / assets",
  "lever_t-1": "prior",
  current_ratio_t: "current ratio",
  "current_ratio_t-1": "prior",
  shares_t: "diluted shares",
  "shares_t-1": "prior",
  gross_margin_t: "gross margin",
  "gross_margin_t-1": "prior",
  turnover_t: "turnover",
  "turnover_t-1": "prior",
};

function fmtInput(k: string, v: number | null): string {
  if (v == null) return "—";
  if (k.startsWith("shares")) return `${fmtNum(v / 1e6, 0)}M`;
  if (k.startsWith("current_ratio") || k.startsWith("turnover")) return fmtNum(v, 2);
  return fmtPct(v, 1);
}

export function QualityTab({ ticker, compact }: { ticker: string; compact?: boolean }) {
  const q = useScores(ticker);
  if (q.isError || q.isLoading || !q.data)
    return <Panel title="Quality & distress scores" subtitle="Piotroski F, Altman Z, Beneish M, Sloan accruals and Ohlson O from the latest 10-K." query={q} compact={compact} skeletonHeight={420} />;
  const d = q.data;
  const fy = d.piotroski.fiscal_years?.[0] ?? d.altman_z.fiscal_year;
  return (
    <div className="stack-lg">
      <p className="co-lede">
        Five classic accounting models, each built from the latest fiscal year{fy ? <> (<span className="num">{fmtDate(fy)}</span>)</> : null}. They were estimated on thousands of past companies to answer three questions:{" "}
        <em>is the business getting stronger</em> (Piotroski), <em>could it fail</em> (Altman, Ohlson) and <em>are the earnings trustworthy</em> (Beneish, Sloan). They are screens, not verdicts.
      </p>
      <div className="grid-2">
        <PiotroskiPanel d={d} />
        <div className="stack">
          <AltmanPanel title="Altman Z-score" info={INFO.altman_z} a={d.altman_z} subtitle="Bankruptcy risk for public manufacturers, using today's market value of equity." min={0} max={5} />
          <AltmanPanel title="Altman Z″" info={INFO.altman_z2} a={d.altman_z2} subtitle="The book-equity variant for non-manufacturers and service firms — usually the better fit for a modern company." min={-2} max={8} />
        </div>
      </div>
      <div className="grid-2">
        <BeneishPanel d={d} />
        <div className="stack">
          <SloanPanel d={d} />
          <OhlsonPanel d={d} />
        </div>
      </div>
      <Panel<Scores> title="How the scores have evolved" subtitle="Each score recomputed at every past fiscal year with only the data available then — trends matter more than any single reading." query={q} skeletonHeight={240}>
        {() => <ScoreHistory d={d} />}
      </Panel>
    </div>
  );
}

function PiotroskiPanel({ d }: { d: Scores }) {
  const p = d.piotroski;
  const sigs: PiotroskiSignal[] = Array.isArray(p.signals) ? p.signals : [];
  const groups = ["Profitability", "Leverage & liquidity", "Operating efficiency"] as const;
  const score = p.score ?? p.partial_score ?? null;
  const tone = p.score == null ? "" : p.score >= 8 ? "gain" : p.score <= 2 ? "loss" : "warn";
  return (
    <Panel title="Piotroski F-score" subtitle="Nine yes/no tests of whether the fundamentals improved this year. 8–9 is strong, 0–2 weak." info={INFO.piotroski} notes={p.missing.length ? [`Unavailable inputs: ${p.missing.join(", ")}`] : []}>
      {sigs.length === 0 ? (
        <div className="subtle small">Needs three consecutive fiscal years of filings; {p.missing.join(", ")}.</div>
      ) : (
        <>
          <div className="co-score-head">
            <div className={`co-score-big num ${tone}`}>
              {score ?? "—"}
              <span className="co-score-of">/ 9</span>
            </div>
            <div>
              <div className="co-score-verdict">{p.interpretation ? p.interpretation.replace(/\s*\(.*\)/, "") : `${p.n_available ?? 0} of 9 signals available`}</div>
              <div className="co-pips" aria-hidden>
                {sigs.map((s) => (
                  <span key={s.name} className={`co-pip ${s.value === 1 ? "on" : s.value === 0 ? "off" : "na"}`} />
                ))}
              </div>
              {p.fiscal_years && <div className="subtle small num">FY {p.fiscal_years.map((y) => y.slice(0, 4)).join(" vs ")}</div>}
            </div>
          </div>
          {groups.map((g) => (
            <div key={g} className="co-check-group">
              <div className="co-mini-label">{g}</div>
              <ul className="co-checklist">
                {sigs
                  .filter((s) => SIGNAL_TEXT[s.name]?.group === g)
                  .map((s) => (
                    <li key={s.name} className={s.value === 1 ? "pass" : s.value === 0 ? "fail" : "na"}>
                      <span className="co-check-icon">{s.value === 1 ? <Icon name="check" size={14} /> : s.value === 0 ? <Icon name="x" size={14} /> : <Icon name="minus" size={14} />}</span>
                      <div className="co-check-body">
                        <div className="co-check-title">
                          {SIGNAL_TEXT[s.name]?.title ?? s.name} <span className="subtle num small">{s.name}</span>
                        </div>
                        <div className="subtle small">{SIGNAL_TEXT[s.name]?.why ?? s.description}</div>
                      </div>
                      <div className="co-check-inputs num small">
                        {Object.entries(s.inputs).map(([k, v]) => (
                          <span key={k}>
                            <span className="subtle">{INPUT_LABEL[k] ?? k}</span> {fmtInput(k, v)}
                          </span>
                        ))}
                      </div>
                    </li>
                  ))}
              </ul>
            </div>
          ))}
        </>
      )}
    </Panel>
  );
}

const ALTMAN_X: Record<string, string> = {
  X1: "Working capital / assets",
  X2: "Retained earnings / assets",
  X3: "EBIT / assets",
  X4: "Equity value / liabilities",
  X5: "Sales / assets",
};

function AltmanPanel({ title, subtitle, info, a, min, max }: { title: string; subtitle: string; info: (typeof INFO)[string]; a: Altman; min: number; max: number }) {
  const lo = a.zones?.distress_below ?? 1.81;
  const hi = a.zones?.safe_above ?? 2.99;
  const comps = a.components ?? [];
  const maxC = Math.max(1e-9, ...comps.map((c) => Math.abs(c.contribution ?? 0)));
  return (
    <Panel title={title} subtitle={subtitle} info={info} notes={a.missing.length ? [`Unavailable inputs: ${a.missing.join(", ")}`] : []}>
      <div className="co-z-head">
        <div className={`co-score-big num ${a.zone === "safe" ? "gain" : a.zone === "distress" ? "loss" : a.zone === "grey" ? "warn" : ""}`}>{a.z != null ? fmtNum(a.z, 2) : "—"}</div>
        <div className="co-score-verdict">{a.zone ? `${a.zone[0].toUpperCase()}${a.zone.slice(1)} zone` : "Not computable"}</div>
      </div>
      <ZoneGauge
        min={min}
        max={max}
        value={a.z}
        format={(v) => fmtNum(v, 2)}
        zones={[
          { from: min, to: lo, tone: "loss", label: "distress" },
          { from: lo, to: hi, tone: "warn", label: "grey" },
          { from: hi, to: max, tone: "gain", label: "safe" },
        ]}
        markers={[
          { at: lo, label: fmtNum(lo, 2) },
          { at: hi, label: fmtNum(hi, 2) },
        ]}
      />
      {comps.length > 0 && (
        <table className="co-comp">
          <thead>
            <tr>
              <th>Ratio</th>
              <th className="num">Value</th>
              <th className="num">× weight</th>
              <th className="num">= points</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {comps.map((c) => (
              <tr key={c.name}>
                <td>
                  <span className="num subtle">{c.name}</span> {c.name === "X4" && title.includes("″") ? "Book equity / liabilities" : ALTMAN_X[c.name]}
                </td>
                <td className="num">{fmtNum(c.value, 3)}</td>
                <td className="num subtle">{fmtNum(c.coefficient, 2)}</td>
                <td className="num">{fmtNum(c.contribution, 2)}</td>
                <td className="co-comp-bar">
                  <ContributionBar value={c.contribution} max={maxC} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function BeneishPanel({ d }: { d: Scores }) {
  const b = d.beneish;
  const idx: Component[] = b.indices ?? [];
  const maxC = Math.max(1e-9, ...idx.map((c) => Math.abs(c.contribution ?? 0)));
  const flagged = b.flag === true;
  return (
    <Panel title="Beneish M-score" subtitle="Does this year's accounting look like that of known earnings manipulators? Each index compares this year with last; 1.0 means no change." info={INFO.beneish} notes={b.missing.length ? [`Unavailable indices: ${b.missing.join(", ")}`] : []}>
      <div className="co-z-head">
        <div className={`co-score-big num ${b.m == null ? "" : flagged ? "loss" : b.flag_sensitive ? "warn" : "gain"}`}>{b.m != null ? fmtNum(b.m, 2) : "—"}</div>
        <div>
          <div className="co-score-verdict">{b.m == null ? "Not computable" : flagged ? "Resembles manipulators (M > −1.78)" : b.flag_sensitive ? "Caught by the stricter −2.22 screen only" : "Unlike manipulators"}</div>
          {b.probability != null && <div className="subtle small">Model probability Φ(M) = <span className="num">{fmtPct(b.probability, 1)}</span></div>}
        </div>
      </div>
      <ZoneGauge
        min={-4}
        max={0}
        value={b.m}
        format={(v) => fmtNum(v, 2)}
        zones={[
          { from: -4, to: -2.22, tone: "gain", label: "unlike" },
          { from: -2.22, to: -1.78, tone: "warn", label: "watch" },
          { from: -1.78, to: 0, tone: "loss", label: "flag" },
        ]}
        markers={[
          { at: -2.22, label: "−2.22" },
          { at: -1.78, label: "−1.78" },
        ]}
      />
      {idx.length > 0 && (
        <table className="co-comp">
          <thead>
            <tr>
              <th>Index</th>
              <th className="num">Value</th>
              <th className="num">× coef.</th>
              <th className="num">= points</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {idx.map((c) => (
              <tr key={c.name}>
                <td>
                  <span className="num">{c.name}</span> <span className="subtle small">{c.description}</span>
                </td>
                <td className={`num ${c.name !== "TATA" && c.value != null && Math.abs(c.value - 1) > 0.15 ? "warn" : ""}`}>{fmtNum(c.value, 3)}</td>
                <td className="num subtle">{fmtNum(c.coefficient, 3)}</td>
                <td className="num">{fmtNum(c.contribution, 2)}</td>
                <td className="co-comp-bar">
                  <ContributionBar value={c.contribution} max={maxC} />
                </td>
              </tr>
            ))}
            <tr className="co-comp-total">
              <td>Intercept + sum</td>
              <td />
              <td className="num subtle">{fmtNum(b.intercept, 2)}</td>
              <td className="num">{fmtNum(b.m, 2)}</td>
              <td />
            </tr>
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function SloanPanel({ d }: { d: Scores }) {
  const s = d.sloan;
  return (
    <Panel title="Accruals (Sloan)" subtitle="How much of reported profit is not yet cash. Large positive accruals have historically preceded weaker earnings and returns." info={INFO.sloan} notes={s.missing.length ? [`Unavailable inputs: ${s.missing.join(", ")}`] : []}>
      <div className="co-z-head">
        <div className={`co-score-big num ${s.ratio == null ? "" : s.ratio > 0.1 ? "loss" : s.ratio < -0.1 ? "gain" : ""}`}>{fmtPct(s.ratio, 1)}</div>
        <div className="co-score-verdict">{s.interpretation ? s.interpretation[0].toUpperCase() + s.interpretation.slice(1) : "Not computable"}</div>
      </div>
      <ZoneGauge
        min={-0.3}
        max={0.3}
        value={s.ratio}
        format={(v) => fmtPct(v, 0)}
        zones={[
          { from: -0.3, to: -0.1, tone: "gain", label: "cash-rich" },
          { from: -0.1, to: 0.1, tone: "neutral", label: "moderate" },
          { from: 0.1, to: 0.3, tone: "loss", label: "accrual-heavy" },
        ]}
      />
      {s.inputs && (
        <div className="co-inline-inputs num small subtle">
          NI {fmtNum((s.inputs.net_income ?? NaN) / 1e6, 0)}M − CFO {fmtNum((s.inputs.cfo ?? NaN) / 1e6, 0)}M over average assets {fmtNum((((s.inputs["total_assets_t"] ?? NaN) + (s.inputs["total_assets_t-1"] ?? NaN)) / 2) / 1e6, 0)}M
        </div>
      )}
    </Panel>
  );
}

function OhlsonPanel({ d }: { d: Scores }) {
  const o = d.ohlson;
  return (
    <Panel title="Ohlson O-score" subtitle="A logit model's one-year probability of bankruptcy from nine accounting variables." info={INFO.ohlson} notes={o.missing.length ? [`Unavailable inputs: ${o.missing.join(", ")}`] : []}>
      <div className="co-z-head">
        <div className={`co-score-big num ${o.probability == null ? "" : o.probability > 0.5 ? "loss" : o.probability > 0.1 ? "warn" : "gain"}`}>{o.probability != null ? fmtPct(o.probability, 1) : "—"}</div>
        <div>
          <div className="co-score-verdict">{o.o != null ? <>O = <span className="num">{fmtNum(o.o, 2)}</span></> : "Not computable"}</div>
          <Formula tex="P(\text{fail}) = \frac{1}{1 + e^{-O}}" inline />
        </div>
      </div>
    </Panel>
  );
}

function ScoreHistory({ d }: { d: Scores }) {
  const h = d.history;
  if (h.length < 2) return <div className="subtle small">Needs at least three fiscal years of filings for a history.</div>;
  const x = h.map((r) => r.fiscal_year);
  return (
    <div className="grid-4 co-hist">
      <div>
        <div className="co-mini-label">Piotroski F (0–9)</div>
        <TimeSeriesChart series={[{ name: "F-score", x, y: h.map((r) => r.piotroski) }]} yFormat="int" height={170} compact layout={{ yaxis: { range: [0, 9.5] } } as any} />
      </div>
      <div>
        <div className="co-mini-label">Altman Z″</div>
        <TimeSeriesChart series={[{ name: "Z''", x, y: h.map((r) => r.altman_z2) }]} yFormat="num" digits={2} height={170} compact baseline={1.1} />
      </div>
      <div>
        <div className="co-mini-label">Beneish M (flag above −1.78)</div>
        <TimeSeriesChart series={[{ name: "M", x, y: h.map((r) => r.beneish_m) }]} yFormat="num" digits={2} height={170} compact baseline={-1.78} />
      </div>
      <div>
        <div className="co-mini-label">Sloan accruals</div>
        <TimeSeriesChart series={[{ name: "Accruals", x, y: h.map((r) => r.sloan_accruals) }]} yFormat="pct" digits={1} height={170} compact baseline={0} />
      </div>
    </div>
  );
}
