/**
 * SCORES — GET /api/fundamentals/{t}/scores: five accounting screens from the latest fiscal
 * years. Each Cell leads with its reading and zone, then the scale, then the terms that make it.
 * Piotroski F (nine tests), Altman Z / Z″ (zones), Beneish M (eight indices), Sloan accruals,
 * Ohlson O, and each score recomputed at past fiscal years. Screens, not verdicts: the zone
 * label is set in signal only on the breach side (distress, manipulator-like, accrual-heavy).
 */
import type { ReactNode } from "react";
import { XYChart } from "../../charts/XYChart";
import { DataTable, Panel, type Column } from "../../components";
import { Note } from "../../design";
import { EM_DASH, fmtNum, fmtPct } from "../../lib/format";
import { ContributionBar, KV, ZoneTrack, useScores } from "./shared";
import type { Altman, Component, PiotroskiSignal, Scores } from "./types";

export const SIGNAL_TEXT: Record<string, { title: string; group: "PROFIT" | "LEVERAGE" | "EFFICIENCY" }> = {
  F_ROA: { title: "ROA > 0", group: "PROFIT" },
  F_CFO: { title: "CFO > 0", group: "PROFIT" },
  F_dROA: { title: "ΔROA > 0", group: "PROFIT" },
  F_ACCRUAL: { title: "CFO > NI", group: "PROFIT" },
  F_dLEVER: { title: "ΔLTD / ASSETS < 0", group: "LEVERAGE" },
  F_dLIQUID: { title: "ΔCURRENT RATIO > 0", group: "LEVERAGE" },
  F_EQ_OFFER: { title: "NO DILUTION", group: "LEVERAGE" },
  F_dMARGIN: { title: "ΔGROSS MARGIN > 0", group: "EFFICIENCY" },
  F_dTURN: { title: "ΔASSET TURNOVER > 0", group: "EFFICIENCY" },
};

const INPUT_LABEL: Record<string, string> = {
  roa_t: "ROA",
  "roa_t-1": "PRIOR",
  cfo_to_assets_t: "CFO/A",
  lever_t: "LTD/A",
  "lever_t-1": "PRIOR",
  current_ratio_t: "CR",
  "current_ratio_t-1": "PRIOR",
  shares_t: "SHARES",
  "shares_t-1": "PRIOR",
  gross_margin_t: "GM",
  "gross_margin_t-1": "PRIOR",
  turnover_t: "TURN",
  "turnover_t-1": "PRIOR",
};

function fmtInput(k: string, v: number | null): string {
  if (v == null) return EM_DASH;
  if (k.startsWith("shares")) return `${fmtNum(v / 1e6, 0)}M`;
  if (k.startsWith("current_ratio") || k.startsWith("turnover")) return fmtNum(v, 2);
  return fmtPct(v, 1);
}

const fy = (ys: string[] | undefined) => (ys?.length ? ys.map((y) => `FY${y.slice(2, 4)}`).join(" VS ") : undefined);
const missingNote = (m: string[]) => (m.length ? [`Unavailable inputs: ${m.join(", ")}`] : []);

/** The reading a score Cell leads with: the figure, its scale, and the zone label. */
function Reading({ value, of, zone, breach, detail }: { value: string; of?: string; zone: string; breach?: boolean; detail?: ReactNode }) {
  return (
    <div className="co-reading">
      <div className={`co-reading-fig num ${breach ? "loss" : ""}`}>
        {value}
        {of && <span className="co-reading-of">{of}</span>}
      </div>
      <div className="co-reading-zone">
        <span className={breach ? "loss" : ""}>{zone}</span>
        {detail && <span className="co-reading-detail num">{detail}</span>}
      </div>
    </div>
  );
}

export function QualityTab({ ticker }: { ticker: string }) {
  const q = useScores(ticker);
  return (
    <div className="stack">
      <div className="grid-2">
        <Panel<Scores>
          title={
            <>
              PIOTROSKI F{q.data && fy(q.data.piotroski.fiscal_years) ? ` · ${fy(q.data.piotroski.fiscal_years)}` : ""}
              <Note n={1} to="piotroski" />
            </>
          }
          query={q}
          skeletonHeight={360}
          notes={q.data ? missingNote(q.data.piotroski.missing) : []}
          provenance={[]}
        >
          {(d) => <Piotroski d={d} />}
        </Panel>
        <div className="stack">
          <Panel<Scores>
            title={
              <>
                ALTMAN Z · MANUFACTURERS{q.data?.altman_z.fiscal_year ? ` · FY${q.data.altman_z.fiscal_year.slice(2, 4)}` : ""}
                <Note n={2} to="altman" />
              </>
            }
            query={q}
            skeletonHeight={200}
            notes={q.data ? missingNote(q.data.altman_z.missing) : []}
            provenance={[]}
          >
            {(d) => <AltmanBody a={d.altman_z} min={0} max={5} z2={false} />}
          </Panel>
          <Panel<Scores>
            title={
              <>
                ALTMAN Z″ · NON-MANUFACTURERS{q.data?.altman_z2.fiscal_year ? ` · FY${q.data.altman_z2.fiscal_year.slice(2, 4)}` : ""}
                <Note n={2} to="altman" />
              </>
            }
            query={q}
            skeletonHeight={200}
            notes={q.data ? missingNote(q.data.altman_z2.missing) : []}
            provenance={[]}
          >
            {(d) => <AltmanBody a={d.altman_z2} min={-2} max={8} z2 />}
          </Panel>
        </div>
      </div>
      <div className="grid-2">
        <Panel<Scores>
          title={
            <>
              BENEISH M{q.data && fy(q.data.beneish.fiscal_years) ? ` · ${fy(q.data.beneish.fiscal_years)}` : ""}
              <Note n={3} to="beneish" />
            </>
          }
          query={q}
          skeletonHeight={360}
          notes={q.data ? missingNote(q.data.beneish.missing) : []}
          provenance={[]}
        >
          {(d) => <Beneish d={d} />}
        </Panel>
        <div className="stack">
          <Panel<Scores>
            title={
              <>
                SLOAN ACCRUALS
                <Note n={4} to="accruals-ohlson" />
              </>
            }
            query={q}
            skeletonHeight={160}
            notes={q.data ? missingNote(q.data.sloan.missing) : []}
            provenance={[]}
          >
            {(d) => <Sloan d={d} />}
          </Panel>
          <Panel<Scores>
            title={
              <>
                OHLSON O · P(FAIL 1Y)
                <Note n={4} to="accruals-ohlson" />
              </>
            }
            query={q}
            skeletonHeight={120}
            notes={q.data ? missingNote(q.data.ohlson.missing) : []}
            provenance={[]}
          >
            {(d) => <Ohlson d={d} />}
          </Panel>
        </div>
      </div>
      <Panel<Scores> title="HISTORY · BY FISCAL YEAR · POINT IN TIME" query={q} skeletonHeight={200}>
        {(d) => <ScoreHistory d={d} />}
      </Panel>
    </div>
  );
}

interface SigRow extends PiotroskiSignal {
  group: string;
}

function Piotroski({ d }: { d: Scores }) {
  const p = d.piotroski;
  const sigs: PiotroskiSignal[] = Array.isArray(p.signals) ? p.signals : [];
  if (!sigs.length) return <div className="co-none">NEEDS 3 CONSECUTIVE FISCAL YEARS · {p.missing.join(" · ").toUpperCase()}</div>;
  const score = p.score ?? p.partial_score ?? null;
  const zone = p.score == null ? `PARTIAL · ${p.n_available ?? 0}/9 AVAILABLE` : p.score >= 8 ? "STRONG · 8–9" : p.score <= 2 ? "WEAK · 0–2" : "MIDDLE · 3–7";
  const order = ["PROFIT", "LEVERAGE", "EFFICIENCY"];
  const rows: SigRow[] = sigs.map((s) => ({ ...s, group: SIGNAL_TEXT[s.name]?.group ?? "" })).sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group));
  const cols: Column<SigRow>[] = [
    { key: "name", label: "CODE", sortable: false, render: (r) => <span className="num">{r.name}</span> },
    { key: "group", label: "GROUP", sortable: false, hideBelow: 600, render: (r) => <span className="co-dim">{r.group}</span> },
    { key: "test", label: "TEST", sortable: false, render: (r) => <span title={r.description}>{SIGNAL_TEXT[r.name]?.title ?? r.description}</span> },
    {
      key: "inputs",
      label: "INPUTS",
      sortable: false,
      hideBelow: 900,
      render: (r) => (
        <span className="co-inputs num">
          {Object.entries(r.inputs).map(([k, v]) => (
            <span key={k}>
              <span className="co-dim">{INPUT_LABEL[k] ?? k}</span> {fmtInput(k, v)}
            </span>
          ))}
        </span>
      ),
    },
    { key: "value", label: "F", numeric: true, sortable: false, render: (r) => <span className="co-strong">{r.value == null ? EM_DASH : r.value}</span> },
  ];
  return (
    <>
      <Reading value={score == null ? EM_DASH : String(score)} of=" / 9" zone={zone} breach={p.score != null && p.score <= 2} />
      <DataTable columns={cols} rows={rows} rowKey={(r) => r.name} scrollLabel="Piotroski tests" />
    </>
  );
}

const ALTMAN_X: Record<string, string> = {
  X1: "WORKING CAPITAL / ASSETS",
  X2: "RETAINED EARNINGS / ASSETS",
  X3: "EBIT / ASSETS",
  X4: "MKT EQUITY / LIABILITIES",
  X5: "SALES / ASSETS",
};

function TermsTable({ rows, label, total }: { rows: Component[]; label: (c: Component) => ReactNode; total?: { k: string; coef: number | null | undefined; sum: number | null } }) {
  const maxC = Math.max(1e-9, ...rows.map((c) => Math.abs(c.contribution ?? 0)));
  type R = Component & { _total?: boolean };
  const all: R[] = total ? [...rows, { name: total.k, value: null, coefficient: total.coef ?? NaN, contribution: total.sum, _total: true }] : rows;
  const cols: Column<R>[] = [
    { key: "name", label: "TERM", sortable: false, render: (c) => (c._total ? <span className="co-strong">{c.name}</span> : <span>{label(c)}</span>) },
    { key: "value", label: "VALUE", numeric: true, sortable: false, render: (c) => (c._total ? "" : fmtNum(c.value, 3)) },
    { key: "coefficient", label: "× COEF", numeric: true, sortable: false, render: (c) => <span className="co-dim">{fmtNum(c.coefficient, 3)}</span> },
    { key: "contribution", label: "= PTS", numeric: true, sortable: false, render: (c) => <span className={c._total ? "co-strong" : ""}>{fmtNum(c.contribution, 2)}</span> },
    { key: "bar", label: <span className="sr-only">BAR</span>, sortable: false, hideBelow: 600, render: (c) => (c._total ? null : <ContributionBar value={c.contribution} max={maxC} />) },
  ];
  return <DataTable columns={cols} rows={all} rowKey={(c) => c.name} scrollLabel="Score terms" />;
}

function AltmanBody({ a, min, max, z2 }: { a: Altman; min: number; max: number; z2: boolean }) {
  const lo = a.zones?.distress_below ?? (z2 ? 1.1 : 1.81);
  const hi = a.zones?.safe_above ?? (z2 ? 2.6 : 2.99);
  const comps = a.components ?? [];
  return (
    <>
      <Reading value={a.z != null ? fmtNum(a.z, 2) : EM_DASH} zone={a.zone ? `${a.zone.toUpperCase()} ZONE` : "NOT COMPUTABLE"} breach={a.zone === "distress"} detail={`DISTRESS < ${fmtNum(lo, 2)} · SAFE > ${fmtNum(hi, 2)}`} />
      <ZoneTrack
        min={min}
        max={max}
        value={a.z}
        format={(v) => fmtNum(v, 2)}
        ticks={[lo, hi]}
        zones={[
          { from: min, to: lo, label: "DISTRESS", breach: true },
          { from: lo, to: hi, label: "GREY" },
          { from: hi, to: max, label: "SAFE" },
        ]}
      />
      {comps.length > 0 && <TermsTable rows={comps} label={(c) => <><span className="num co-dim">{c.name}</span> {c.name === "X4" && z2 ? "BOOK EQUITY / LIABILITIES" : ALTMAN_X[c.name] ?? ""}</>} />}
    </>
  );
}

function Beneish({ d }: { d: Scores }) {
  const b = d.beneish;
  const idx: Component[] = b.indices ?? [];
  const flagged = b.flag === true;
  const zone = b.m == null ? "NOT COMPUTABLE" : flagged ? "MANIPULATOR-LIKE · M > −1.78" : b.flag_sensitive ? "WATCH · M > −2.22" : "UNLIKE MANIPULATORS";
  return (
    <>
      <Reading value={b.m != null ? fmtNum(b.m, 2) : EM_DASH} zone={zone} breach={flagged} detail={b.probability != null ? `Φ(M) ${fmtPct(b.probability, 1)}` : undefined} />
      <ZoneTrack
        min={-4}
        max={0}
        value={b.m}
        format={(v) => fmtNum(v, 2)}
        ticks={[-2.22, -1.78]}
        zones={[
          { from: -4, to: -2.22, label: "UNLIKE" },
          { from: -2.22, to: -1.78, label: "WATCH" },
          { from: -1.78, to: 0, label: "FLAG", breach: true },
        ]}
      />
      {idx.length > 0 && <TermsTable rows={idx} label={(c) => <span title={c.description} className="num">{c.name}</span>} total={{ k: "INTERCEPT + SUM", coef: b.intercept, sum: b.m }} />}
    </>
  );
}

function Sloan({ d }: { d: Scores }) {
  const s = d.sloan;
  const heavy = s.ratio != null && s.ratio > 0.1;
  const i = s.inputs;
  return (
    <>
      <Reading value={fmtPct(s.ratio, 1)} zone={s.interpretation ? s.interpretation.toUpperCase() : "NOT COMPUTABLE"} breach={heavy} detail="OF AVG ASSETS" />
      <ZoneTrack
        min={-0.3}
        max={0.3}
        value={s.ratio}
        format={(v) => fmtPct(v, 0)}
        ticks={[-0.1, 0.1]}
        zones={[
          { from: -0.3, to: -0.1, label: "CASH-RICH" },
          { from: -0.1, to: 0.1, label: "MODERATE" },
          { from: 0.1, to: 0.3, label: "ACCRUAL-HEAVY", breach: true },
        ]}
      />
      {i && (
        <KV
          rows={[
            { k: "NET INCOME", v: `$${fmtNum((i.net_income ?? NaN) / 1e6, 0)}M` },
            { k: "CFO", v: `$${fmtNum((i.cfo ?? NaN) / 1e6, 0)}M` },
            { k: "AVG ASSETS", v: `$${fmtNum(((i["total_assets_t"] ?? NaN) + (i["total_assets_t-1"] ?? NaN)) / 2 / 1e6, 0)}M` },
          ]}
        />
      )}
    </>
  );
}

function Ohlson({ d }: { d: Scores }) {
  const o = d.ohlson;
  const p = o.probability;
  return <Reading value={p != null ? fmtPct(p, 1) : EM_DASH} zone={o.o != null ? `O ${fmtNum(o.o, 2)}` : "NOT COMPUTABLE"} breach={p != null && p > 0.5} detail="P = 1 / (1 + e^−O)" />;
}

function ScoreHistory({ d }: { d: Scores }) {
  const h = d.history;
  if (h.length < 2) return <div className="co-none">NEEDS 3+ FISCAL YEARS OF FILINGS</div>;
  const x = h.map((r) => r.fiscal_year);
  const mini = (title: string, name: string, y: (number | null)[], fmt: "int" | "num" | "pct", rule?: { at: number; label: string }) => (
    <div>
      <div className="co-ctl-k">{title}</div>
      <XYChart time x={x} series={[{ name, y, mode: "line" }]} yFormat={fmt} digits={fmt === "pct" ? 1 : 2} height={160} hlines={rule ? [{ ...rule, tone: "signal", dash: "dot" }] : []} ariaLabel={`${title} by fiscal year`} />
    </div>
  );
  return (
    <div className="grid-4 co-hist">
      {mini("PIOTROSKI F · 0–9", "F", h.map((r) => r.piotroski), "int")}
      {mini("ALTMAN Z″", "Z″", h.map((r) => r.altman_z2), "num", { at: 1.1, label: "1.10" })}
      {mini("BENEISH M", "M", h.map((r) => r.beneish_m), "num", { at: -1.78, label: "−1.78" })}
      {mini("SLOAN ACCRUALS", "ACC", h.map((r) => r.sloan_accruals), "pct", { at: 0.1, label: "10%" })}
    </div>
  );
}

