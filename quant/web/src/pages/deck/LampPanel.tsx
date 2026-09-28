/**
 * Instrument C — the lamp panel (after the banks of square lamps in the 1968/1977 cockpits):
 * is the data behind the deck alive? One square lamp per feed the reading reports, a bank of
 * limit-state lamps, and four seven-segment counters: day P&L, VaR, the oldest mark's age,
 * and the time to the close (or the open) from the session clock.
 */
import { useEffect, useMemo, useState } from "react";
import { fmtPct } from "../../lib/format";
import type { DeckModel, FeedState } from "./model";
import { SevenSeg, segWidth } from "./SevenSeg";
import { ageCounter, moneyCounter, sessionCountdown, type Counter } from "./segments";

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

function CounterTile({ label, counter, caption, signed = false }: { label: string; counter: Counter; caption?: string; signed?: boolean }) {
  const w = 14;
  const h = 24;
  const gap = 5;
  const width = Math.max(segWidth(counter.digits, w, gap), 60) + 16;
  return (
    <div className={`dk-counter${signed && counter.sign ? ` ${counter.sign}` : ""}`}>
      <div className="dk-counter-label">{label}</div>
      <svg viewBox={`0 0 ${width} ${h + 12}`} role="img" aria-label={`${label}: ${counter.text}`} className="dk-counter-svg" style={{ maxWidth: width * 1.6 }}>
        <SevenSeg text={counter.digits} x={(width - segWidth(counter.digits, w, gap)) / 2} y={6} w={w} h={h} gap={gap} />
      </svg>
      <div className="dk-counter-unit">
        {counter.unit}
        {caption ? ` · ${caption}` : ""}
      </div>
    </div>
  );
}

export function Counters({ model }: { model: DeckModel }) {
  const now = useNow(15_000);
  const responseTime = Date.parse(model.as_of ?? "");
  const elapsed = Number.isFinite(responseTime) ? Math.max(0, (now - responseTime) / 1000) : 0;
  const c = model.counters;
  const countdown = sessionCountdown(model.clock, now);
  const alpha = model.alpha != null ? `${fmtPct(model.alpha, 0)} 1-day` : "1-day";

  return (
      <div className="dk-counters">
        <CounterTile signed label="Day P&L" counter={moneyCounter(c.day_pnl_usd)} caption={c.day_pnl != null ? fmtPct(c.day_pnl, 2, { signed: true }) : undefined} />
        <CounterTile label={`VaR · ${alpha}`} counter={moneyCounter(c.var_usd)} />
        <CounterTile label="Oldest mark" counter={ageCounter(c.marks_age_s == null ? null : c.marks_age_s + elapsed)} />
        <CounterTile label={countdown.label === "session clock" ? "Session" : countdown.label === "to the close" ? "To the close" : "To the open"} counter={countdown} caption={model.clock ? (model.clock.is_open ? "open" : "closed") : "no clock"} />
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
      { key: "near", label: "Near (≥ 80 %)", n: near, tone: "near" },
      { key: "over", label: "Breached", n: over, tone: "over" },
      { key: "unknown", label: "Unevaluated", n: model.unevaluated.length, tone: "unknown" },
    ];
  }, [model.limits, model.unevaluated]);

  return (
    <div className="dk-console dk-lamps">


      <div className="dk-bank-title">Feeds</div>
      <ul className="dk-bank" aria-label="Data feeds">
        {model.feeds.map((f) => (
          <li key={f.key} className={`dk-bank-item ${stale && f.state === "ok" ? "stale" : f.state}`} title={[f.detail, ageWords(f.age_s)].filter(Boolean).join(" · ")}>
            <span className="dk-square" aria-hidden="true" />
            <button className="dk-bank-label" onClick={() => onInspect(`${f.label}: ${stale ? "refresh failed · " : ""}${f.detail ?? f.state}${f.age_s != null ? ` · ${ageWords(f.age_s + elapsed)}` : ""}`)}>{f.label}</button>
            <span className="dk-bank-state">
              {stale && f.state === "ok" ? "stale" : STATE_WORD[f.state] ?? f.state}
              <span className="sr-only">{f.detail ? ` — ${f.detail}` : ""}{f.age_s != null ? `, ${ageWords(f.age_s)}` : ""}</span>
            </span>
          </li>
        ))}
        {!model.feeds.length && <li className="dk-bank-item off">No feeds reported.</li>}
      </ul>

      <div className="dk-bank-title">Limits</div>
      <ul className="dk-bank dk-bank-states" aria-label="Limits by state">
        {states.map((s) => (
          <li key={s.key} className={`dk-bank-item ${s.tone}${s.n ? " lit" : " dark"}`}>
            <span className="dk-square" aria-hidden="true" />
            <span className="dk-bank-label">{s.label}</span>
            <span className="dk-bank-state num">{s.n}</span>
          </li>
        ))}
      </ul>
      <p className="dk-console-note">Select a lamp for its source, age and diagnostics.</p>
    </div>
  );
}
