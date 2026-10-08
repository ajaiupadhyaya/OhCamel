/**
 * The active portfolio as one ruled strip: name, a composition rule (segment width = |weight|,
 * shorts in ink-3), the holdings with weights, benchmark / notional / invested / window, and
 * RUN · EDIT · SHARE. The shared <PortfolioBuilder/> opens below it.
 */
import { useState, type CSSProperties } from "react";
import { PortfolioBuilder } from "../../components";
import { fmtCurrency, fmtDate, fmtPct } from "../../lib/format";
import { grossWeight, totalWeight, usePortfolio } from "../../lib/portfolio";

const MAX_LISTED = 10;

export function PortfolioStrip({ dirty, onRun }: { dirty: boolean; onRun: () => void }) {
  const { portfolio: p, shareUrl } = usePortfolio();
  const [open, setOpen] = useState(p.holdings.length === 0);
  const [copied, setCopied] = useState(false);
  const hs = p.holdings.filter((h) => h.weight !== 0);
  const gross = grossWeight(hs) || 1;
  const listed = [...hs].sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight));
  const rest = listed.slice(MAX_LISTED);
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
    <section className={`pl-strip-wrap ${open ? "open" : ""}`} aria-label="Active portfolio">
      <div className="pl-strip">
        <div className="pl-strip-main">
          <div className="pl-strip-top">
            <span className="pl-strip-k">BOOK</span>
            <span className="pl-strip-name">{p.name || "UNTITLED"}</span>
          </div>
          <div className="pl-comp" role="img" aria-label={`Composition: ${listed.map((h) => `${h.ticker} ${fmtPct(h.weight, 1)}`).join(", ")}`}>
            {listed.map((h) => (
              <span key={h.ticker} className={`pl-comp-seg ${h.weight < 0 ? "short" : ""}`} style={{ "--grow": Math.abs(h.weight) / gross } as CSSProperties} />
            ))}
          </div>
          <div className="pl-comp-legend num">
            {listed.slice(0, MAX_LISTED).map((h) => (
              <span key={h.ticker} className="pl-comp-item">
                {h.ticker} <span className="subtle">{fmtPct(h.weight, 1)}</span>
              </span>
            ))}
            {rest.length > 0 && <span className="pl-comp-item subtle">+{rest.length}</span>}
            {hs.length === 0 && <span className="pl-comp-item subtle">NO HOLDINGS</span>}
          </div>
        </div>
        <dl className="pl-strip-meta">
          <div>
            <dt>BENCH</dt>
            <dd className="num">{p.benchmark || "—"}</dd>
          </div>
          <div>
            <dt>NOTIONAL</dt>
            <dd className="num">{fmtCurrency(p.notional, { compact: true })}</dd>
          </div>
          <div>
            <dt>NET</dt>
            <dd className="num">{fmtPct(net, 0)}</dd>
          </div>
          <div>
            <dt>WINDOW</dt>
            <dd className="num">{p.start || p.end ? `${p.start ? fmtDate(p.start, "month") : "START"} – ${p.end ? fmtDate(p.end, "month") : "LATEST"}` : "FULL"}</dd>
          </div>
        </dl>
        <div className="pl-strip-actions">
          {dirty && (
            <button type="button" className="btn btn-sm btn-primary" onClick={onRun} disabled={hs.length === 0}>
              RUN
            </button>
          )}
          <button type="button" className="btn btn-sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            {open ? "CLOSE" : "EDIT"}
          </button>
          <button type="button" className="btn btn-sm" onClick={share}>
            {copied ? "COPIED" : "LINK"}
          </button>
        </div>
      </div>
      {dirty && (
        <div className="pl-dirty num" role="status">
          INPUTS CHANGED · RESULTS ARE FROM THE LAST RUN
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
