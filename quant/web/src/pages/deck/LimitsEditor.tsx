/**
 * The limits editor: the viewer's policy lines, kept in this browser
 * (localStorage "ohcamel.deck.limits.v1") and sent with every reading. Every threshold is
 * edited as a percent: of equity for exposure / drawdown kinds, of NOTIONAL for the money
 * kinds (VaR, ES, day loss), which the server evaluates in dollars.
 */
import { useEffect, useState } from "react";
import { NumberField, Select } from "../../components";
import { Icon } from "../../components/Icon";
import { fmtCurrency } from "../../lib/format";
import { KINDS, isMoneyKind, uniqueName, type LimitIn, type LimitKind } from "./limits";

const LARGEST = "__largest__";

function NameInput({ value, onCommit, taken }: { value: string; onCommit: (v: string) => void; taken: string[] }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const commit = () => {
    const t = text.trim().replace(/\s+/g, "-");
    if (!t || t === value) return setText(value);
    const v = uniqueName(t, taken);
    onCommit(v);
    setText(v);
  };
  return <input className="input dk-limit-name" aria-label="Limit name" value={text} onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />;
}

export function LimitsEditor({ limits, customised, onChange, onReset, notional, tickers }: { limits: LimitIn[]; customised: boolean; onChange: (ls: LimitIn[]) => void; onReset: () => void; notional: number | null; tickers: string[] }) {
  const set = (i: number, patch: Partial<LimitIn>) => onChange(limits.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const remove = (i: number) => onChange(limits.filter((_, j) => j !== i));
  const add = () => onChange([...limits, { name: uniqueName("gross-cap", limits.map((l) => l.name)), kind: "gross", threshold: 1.5 }]);

  return (
    <div className="dk-limits">
      <div className="dk-limits-head subtle small">
        <span>Name</span>
        <span>Kind</span>
        <span>Threshold</span>
        <span>Applies to</span>
        <span />
      </div>
      <ul className="dk-limits-list">
        {limits.map((l, i) => {
          const money = isMoneyKind(l.kind);
          const others = limits.filter((_, j) => j !== i).map((x) => x.name);
          const kindInfo = KINDS.find((k) => k.kind === l.kind);
          return (
            <li key={i} className="dk-limit-row">
              <NameInput value={l.name} taken={others} onCommit={(name) => set(i, { name })} />
              <Select<LimitKind> ariaLabel={`Kind of ${l.name}`} value={l.kind} onChange={(kind) => set(i, { kind, ticker: kind === "name" ? l.ticker : undefined })} options={KINDS.map((k) => ({ value: k.kind, label: k.label }))} />
              <div className="dk-limit-threshold">
                <NumberField value={l.threshold} onChange={(threshold) => set(i, { threshold })} percent min={0} step={money ? 0.0025 : 0.05} width={112} />
                <span className="subtle small">
                  {money ? (
                    <>
                      of notional{notional != null && <span className="num"> = {fmtCurrency(l.threshold * notional, { digits: 0 })}</span>}
                    </>
                  ) : (
                    "of equity"
                  )}
                </span>
              </div>
              <div className="dk-limit-scope">
                {l.kind === "name" ? (
                  <Select<string>
                    ariaLabel={`Ticker for ${l.name}`}
                    value={l.ticker ?? LARGEST}
                    onChange={(t) => set(i, { ticker: t === LARGEST ? undefined : t })}
                    options={[{ value: LARGEST, label: "Largest holding" }, ...[...new Set([...tickers, ...(l.ticker ? [l.ticker] : [])])].map((t) => ({ value: t, label: t }))]}
                  />
                ) : (
                  <span className="subtle small">{kindInfo?.observed}</span>
                )}
              </div>
              <button type="button" className="icon-btn" aria-label={`Remove ${l.name}`} title="Remove this limit" onClick={() => remove(i)}>
                <Icon name="trash" size={15} />
              </button>
            </li>
          );
        })}
      </ul>
      <div className="row row-wrap dk-limits-actions">
        <button type="button" className="btn btn-sm" onClick={add}>
          <Icon name="plus" size={14} /> Add a limit
        </button>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onReset} disabled={!customised}>
          <Icon name="refresh" size={14} /> Reset to defaults
        </button>
        <span className="subtle small">{customised ? "Your limits, saved in this browser." : "The server's defaults. Edit any line to keep your own in this browser."}</span>
      </div>
    </div>
  );
}
