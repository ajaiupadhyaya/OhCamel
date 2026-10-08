/**
 * Instrument C — the lamp panel (after the banks of square lamps in the 1968/1977 cockpits):
 * is the data behind the deck alive? One square lamp per feed the reading reports, a bank of
 * limit-state lamps, and four seven-segment counters: day P&L, VaR, the oldest mark's age,
 * and the time to the close (or the open) from the session clock.
 */
import { useEffect, useMemo, useState } from "react";
import { fmtPct } from "../../lib/format";
import type { DeckModel, FeedState } from "./model";
import { counterBank, type DigitFit } from "./fit";
import { SevenSeg, segWidth } from "./SevenSeg";
import { ageCounter, moneyCounter, sessionCountdown, type Counter } from "./segments";
import { useBox } from "./useBox";

const STATE_WORD: Record<FeedState, string> = { ok: "ready", stale: "stale", down: "down", off: "off", unknown: "unchecked" };

function ageWords(s: number | null): string {
  if (s == null) return "";
  if (s < 90) return `${Math.round(s)} s old`;
  if (s < 5400) return `${Math.round(s / 60)} min old`;
  if (s < 172800) return `${Math.round(s / 3600)} h old`;
  return `${Math.round(s / 86400)} d old`;
}

/** Wall-clock "now", refreshed every `ms` — the countdown's only input besides the clock. */
function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

function CounterTile({ label, counter, seg, caption, signed = false }: { label: string; counter: Counter; seg: DigitFit; caption?: string; signed?: boolean }) {
  const width = Math.max(1, segWidth(counter.digits, seg.w, seg.gap));
  return (
    <div className={`dk-counter${signed && counter.sign ? ` ${counter.sign}` : ""}`}>
      <div className="dk-counter-label">{label}</div>
      <svg width={width} height={seg.h} viewBox={`0 0 ${width} ${seg.h}`} role="img" aria-label={`${label}: ${counter.text}`} className="dk-counter-svg">
        <SevenSeg text={counter.digits} x={0} y={0} w={seg.w} h={seg.h} gap={seg.gap} />
      </svg>
      <div className="dk-counter-unit">
        {counter.unit}
        {caption ? ` · ${caption}` : ""}
      </div>
    </div>
  );
}

/**
 * The counter bank: four seven-segment faces, one per real counter (day P&L, VaR, the oldest
 * mark's age, time to the close or open), all drawn at one digit size set by the longest
 * reading and the row's measured width (fit.ts).
 */
export function Counters({ model }: { model: DeckModel }) {
  const now = useNow(15_000);
  const responseTime = Date.parse(model.as_of ?? "");
  const elapsed = Number.isFinite(responseTime) ? Math.max(0, (now - responseTime) / 1000) : 0;
  const c = model.counters;
  const countdown = sessionCountdown(model.clock, now);
  const alpha = model.alpha != null ? `${fmtPct(model.alpha, 0)} 1-day` : "1-day";
  const pnl = moneyCounter(c.day_pnl_usd);
  const risk = moneyCounter(c.var_usd);
  const age = ageCounter(c.marks_age_s == null ? null : c.marks_age_s + elapsed);
  const [row, size] = useBox<HTMLDivElement>({ width: 600, height: 0 });
  const bank = counterBank(size.width, [pnl.digits.length, risk.digits.length, age.digits.length, countdown.digits.length]);

  return (
    <div className="dk-counters-fit" ref={row}>
      <div className={`dk-counters cols-${bank.cols}`}>
        <CounterTile signed label="Day P&L" counter={pnl} seg={bank.seg} caption={c.day_pnl != null ? fmtPct(c.day_pnl, 2, { signed: true }) : undefined} />
        <CounterTile label={`VaR · ${alpha}`} counter={risk} seg={bank.seg} />
        <CounterTile label="Oldest mark" counter={age} seg={bank.seg} />
        <CounterTile label={countdown.label === "session clock" ? "Session" : countdown.label === "to the close" ? "To the close" : "To the open"} counter={countdown} seg={bank.seg} caption={model.clock ? (model.clock.is_open ? "open" : "closed") : "no clock"} />
      </div>
    </div>
  );
}

export function LampPanel({ model, onInspect, stale }: { model: DeckModel; onInspect: (text: string) => void; stale: boolean }) {
  const now = useNow(15_000);
  const elapsed = Math.max(0, (now - (Date.parse(model.as_of ?? "") || now)) / 1000);
  const states = useMemo(() => {
    const near = model.limits.filter((l) => !l.breached && l.utilisation != null && l.utilisation >= 0.8).length;
    const over = model.limits.filter((l) => l.breached).length;
    const clear = model.limits.length - near - over;
    return [
      { key: "clear", label: "Clear", n: clear, tone: "ok" },
      { key: "near", label: "Near ≥80%", n: near, tone: "near" },
      { key: "over", label: "Breached", n: over, tone: "over" },
      { key: "unknown", label: "Unevaluated", n: model.unevaluated.length, tone: "unknown" },
    ];
  }, [model.limits, model.unevaluated]);

  return (
    <div className="dk-lamps">
      <h3 className="dk-group">FDS<span>feeds</span></h3>
      <ul className="dk-lampfield" aria-label="Data feeds">
        {model.feeds.map((f) => {
          const tone = stale && f.state === "ok" ? "stale" : f.state;
          return (
            <li key={f.key} className={`dk-key ${tone}`} title={[f.detail, ageWords(f.age_s)].filter(Boolean).join(" · ")}>
              <button type="button" className="dk-key-face" onClick={() => onInspect(`${f.label}: ${stale ? "refresh failed · " : ""}${f.detail ?? f.state}${f.age_s != null ? ` · ${ageWords(f.age_s + elapsed)}` : ""}`)}>
                <span className="dk-key-legend">{f.label}</span>
                <span className="dk-key-state">
                  {stale && f.state === "ok" ? "stale" : STATE_WORD[f.state] ?? f.state}
                  <span className="sr-only">{f.detail ? ` — ${f.detail}` : ""}{f.age_s != null ? `, ${ageWords(f.age_s)}` : ""}</span>
                </span>
              </button>
            </li>
          );
        })}
        {!model.feeds.length && (
          <li className="dk-key off">
            <span className="dk-key-face">
              <span className="dk-key-legend">NO FEEDS</span>
              <span className="dk-key-state">reported</span>
            </span>
          </li>
        )}
      </ul>

      <h3 className="dk-group">LIM<span>limits by state</span></h3>
      <ul className="dk-lampfield dk-lampfield-4" aria-label="Limits by state">
        {states.map((s) => (
          <li key={s.key} className={`dk-key ${s.tone}${s.n ? " lit" : " dark"}`}>
            <span className="dk-key-face">
              <span className="dk-key-legend">{s.label}</span>
              <span className="dk-key-state num">{s.n}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
