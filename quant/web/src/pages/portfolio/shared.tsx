/**
 * Small building blocks shared by Portfolio and Risk (prefix `pl-`). Paper Tape: ruled rows,
 * caps labels, mono numbers; signal only for rejections, breaches and losses.
 */
import { useCallback, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { InfoTip, type InfoProp } from "../../components";
import { fmtCurrency, fmtNum, fmtPct } from "../../lib/format";

/**
 * Draft → committed inputs for heavy POSTs. The first draft is committed immediately (so
 * results show on load); later edits only take effect when `run()` is called.
 */
export function useCommitted<T>(draft: T): { committed: T; run: () => void; dirty: boolean } {
  const [committed, setCommitted] = useState<T>(draft);
  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(committed), [draft, committed]);
  const run = useCallback(() => setCommitted(draft), [draft]);
  return { committed, run, dirty };
}

/** RUN for a heavy computation: armed (ink ground) when inputs changed, CURRENT otherwise. */
export function RunButton({ onRun, dirty, busy, label = "RUN", disabled }: { onRun: () => void; dirty: boolean; busy?: boolean; label?: string; disabled?: boolean }) {
  return (
    <button type="button" className={`btn btn-sm pl-run ${dirty ? "btn-primary" : ""}`} onClick={onRun} disabled={disabled || busy || !dirty}>
      {busy ? "RUNNING" : dirty ? label : "CURRENT"}
    </button>
  );
}

/** Label/value rows; InfoTips only on metric labels. */
export function KV({ rows, cols = 1 }: { rows: { label: ReactNode; value: ReactNode; info?: InfoProp; tone?: string; hint?: ReactNode }[]; cols?: 1 | 2 }) {
  return (
    <dl className={`pl-kv ${cols === 2 ? "pl-kv-2" : ""}`}>
      {rows.map((r, i) => (
        <div className="pl-kv-row" key={i}>
          <dt>
            {r.label}
            <InfoTip info={r.info} size={12} label={typeof r.label === "string" ? r.label : undefined} />
          </dt>
          <dd className={`num ${r.tone ?? ""}`}>
            {r.value}
            {r.hint && <span className="pl-kv-hint">{r.hint}</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Basel traffic-light zone as a caps word: RED in signal, the others ink (no green here). */
export function ZoneChip({ zone, title }: { zone: string | null | undefined; title?: string }) {
  if (!zone) return <span className="subtle">—</span>;
  return (
    <span className={`pl-zone num ${zone === "red" ? "loss" : ""}`} title={title}>
      {zone.toUpperCase()}
    </span>
  );
}

/** The Acerbi–Szekely Z2 indicative verdict as a word and its tone: REJECT in signal (as a red zone). */
export function esVerdict(v: string): { word: string; tone: "loss" | "subtle" | "" } {
  const word = v.split(/[\s(]/)[0].toUpperCase();
  return { word, tone: v.startsWith("reject") ? "loss" : v === "accept" ? "" : "subtle" };
}

/** A p-value cell: signal when the model is rejected at 5%. */
export function PValue({ p, digits = 3 }: { p: number | null | undefined; digits?: number }) {
  if (p === null || p === undefined || !Number.isFinite(p)) return <span className="subtle">—</span>;
  const rejected = p < 0.05;
  return (
    <span className={rejected ? "loss" : ""} title={rejected ? "Rejected at 5%" : "Not rejected at 5%"}>
      {p < 0.001 ? "<0.001" : fmtNum(p, digits)}
    </span>
  );
}

/** Percent with dollars beside it. */
export function PctUsd({ pct, usd, digits = 2 }: { pct: number | null | undefined; usd: number | null | undefined; digits?: number }) {
  return (
    <span className="pl-pctusd">
      <span>{fmtPct(pct, digits)}</span>
      <span className="pl-usd">{fmtCurrency(usd, { compact: Math.abs(usd ?? 0) >= 100_000, digits: Math.abs(usd ?? 0) >= 100_000 ? 1 : 0 })}</span>
    </span>
  );
}

/** A terse ruled readout line: KEY value · KEY value (labels, not sentences). */
export function Readline({ items }: { items: { k: string; v: ReactNode; tone?: string }[] }) {
  return (
    <div className="pl-readline num">
      {items.map((it, i) => (
        <span key={i} className="pl-readline-item">
          <span className="pl-readline-k">{it.k}</span> <span className={it.tone ?? ""}>{it.v}</span>
        </span>
      ))}
    </div>
  );
}

/**
 * Capital against risk, one ruled row per holding: two hairline bars on a shared scale (weight
 * in ink-3, risk share in ink) and the difference in points. Sorted by risk share.
 */
export function ShareRows({ rows, riskLabel }: { rows: { ticker: string; weight: number; share: number }[]; riskLabel: string }) {
  const sorted = [...rows].sort((a, b) => b.share - a.share);
  const max = Math.max(0.01, ...sorted.flatMap((r) => [Math.abs(r.weight), Math.abs(r.share)]));
  const w = (v: number) => `${Math.min(100, (Math.abs(v) / max) * 100)}%`;
  return (
    <div className="pl-share-wrap">
      <table className="pl-share">
        <thead>
          <tr>
            <th scope="col">HOLDING</th>
            <th scope="col" className="num-col">WEIGHT</th>
            <th scope="col" className="num-col">{riskLabel}</th>
            <th scope="col" className="num-col">Δ</th>
            <th scope="col" className="pl-share-bars">
              <span className="pl-share-key">
                <span className="pl-share-key-w" /> W <span className="pl-share-key-r" /> R
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.ticker}>
              <th scope="row" className="num">{r.ticker}</th>
              <td className="num">{fmtPct(r.weight, 1)}</td>
              <td className="num">{fmtPct(r.share, 1)}</td>
              <td className="num">{fmtPct(r.share - r.weight, 1, { signed: true })}</td>
              <td className="pl-share-bars" aria-hidden>
                <span className="pl-share-w" style={{ "--w": w(r.weight) } as CSSProperties} />
                <span className="pl-share-r" style={{ "--w": w(r.share) } as CSSProperties} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export const usd = (v: number | null | undefined, signed = false) => fmtCurrency(v, { compact: Math.abs(v ?? 0) >= 10_000, digits: Math.abs(v ?? 0) >= 10_000 ? 1 : 0, signed });

/** Series/frame dates are ISO strings; this keeps charts typed. */
export const asDates = (xs: (string | number)[]) => xs.map(String);
