/**
 * The active portfolio at a glance (composition bar, benchmark, notional, window), with
 * the shared <PortfolioBuilder/> in a collapsible drawer and the "run analysis" control.
 */
import { useState } from "react";
import { Icon, PortfolioBuilder } from "../../components";
import { fmtCurrency, fmtDate, fmtPct } from "../../lib/format";
import { grossWeight, totalWeight, usePortfolio } from "../../lib/portfolio";

const MAX_SEGMENTS = 7; // --c1..--c7 for holdings, the 8th slot is folded into "Other"

export function PortfolioStrip({ dirty, onRun }: { dirty: boolean; onRun: () => void }) {
  const { portfolio: p, shareUrl } = usePortfolio();
  const [open, setOpen] = useState(p.holdings.length === 0);
  const [copied, setCopied] = useState(false);
  // Colours follow the builder's order (slot i = i-th holding) so both views agree.
  const hs = p.holdings.map((h, i) => ({ ...h, slot: i })).filter((h) => h.weight !== 0);
  const gross = grossWeight(hs) || 1;
  const shown = hs.filter((h) => h.slot < (hs.length > MAX_SEGMENTS + 1 ? MAX_SEGMENTS : 8));
  const rest = hs.filter((h) => !shown.includes(h));
  const segs = [
    ...shown.map((h) => ({ key: h.ticker, label: h.ticker, w: h.weight, color: `var(--c${h.slot + 1})`, short: h.weight < 0 })),
    ...(rest.length ? [{ key: "__other", label: `${rest.length} others`, w: rest.reduce((a, h) => a + h.weight, 0), color: "var(--text-3)", short: false }] : []),
  ];
  const net = totalWeight(hs);
  const share = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl());
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      window.prompt("Copy this link", shareUrl());
    }
  };

  return (
    <section className={`pl-strip-wrap ${open ? "open" : ""}`}>
      <div className="pl-strip">
        <div className="pl-strip-main">
          <div className="pl-strip-top">
            <span className="eyebrow">Active portfolio</span>
            <span className="pl-strip-name display">{p.name || "Untitled"}</span>
          </div>
          <div className="pl-comp" role="img" aria-label={`Composition: ${segs.map((s) => `${s.label} ${fmtPct(s.w, 1)}`).join(", ")}`}>
            {segs.map((s) => (
              <span key={s.key} className={`pl-comp-seg ${s.short ? "short" : ""}`} style={{ flexGrow: Math.abs(s.w) / gross, background: s.color }} title={`${s.label} ${fmtPct(s.w, 1)}`} />
            ))}
          </div>
          <div className="pl-comp-legend">
            {segs.map((s) => (
              <span key={s.key} className="pl-comp-item">
                <span className="pl-comp-dot" style={{ background: s.color }} />
                <span className="num">{s.label}</span>
                <span className="num subtle">{fmtPct(s.w, 1)}</span>
              </span>
            ))}
            {hs.length === 0 && <span className="subtle small">No holdings yet — open the editor to add some.</span>}
          </div>
        </div>
        <dl className="pl-strip-meta">
          <div>
            <dt>Benchmark</dt>
            <dd className="num">{p.benchmark || "—"}</dd>
          </div>
          <div>
            <dt>Notional</dt>
            <dd className="num">{fmtCurrency(p.notional, { compact: true })}</dd>
          </div>
          <div>
            <dt>Invested</dt>
            <dd className="num">{fmtPct(net, 0)}</dd>
          </div>
          <div>
            <dt>Window</dt>
            <dd className="num">{p.start || p.end ? `${p.start ? fmtDate(p.start, "month") : "start"} – ${p.end ? fmtDate(p.end, "month") : "latest"}` : "Full history"}</dd>
          </div>
        </dl>
        <div className="pl-strip-actions">
          {dirty && (
            <button type="button" className="btn btn-primary" onClick={onRun} disabled={hs.length === 0}>
              <Icon name="refresh" size={15} /> Run analysis
            </button>
          )}
          <button type="button" className="btn" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            <Icon name={open ? "chevron-up" : "portfolio"} size={15} /> {open ? "Close editor" : "Edit portfolio"}
          </button>
          <button type="button" className="btn btn-ghost" onClick={share} title="Copy a link that opens this exact portfolio">
            <Icon name={copied ? "check" : "share"} size={15} /> {copied ? "Link copied" : "Share"}
          </button>
        </div>
      </div>
      {dirty && (
        <div className="pl-dirty" role="status">
          <span className="pl-dirty-dot" aria-hidden />
          The portfolio has changed since the results below were computed. <button type="button" className="pl-linkbtn" onClick={onRun}>Run analysis</button> to update every tab.
        </div>
      )}
      {open && (
        <div className="pl-editor">
          <PortfolioBuilder />
        </div>
      )}
    </section>
  );
}
