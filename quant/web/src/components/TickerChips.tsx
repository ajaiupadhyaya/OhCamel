/**
 * A row of removable ticker chips, optionally with an inline TickerInput to add more.
 *   <TickerChips tickers={ts} onChange={setTs} addable colored />
 * `colored` adds a dot in the categorical series colour for each position (matches charts
 * that plot tickers in the same order). Positions 9+ get the neutral --c-other (palette
 * hues are never repeated).
 */
import { Link } from "react-router-dom";
import { seriesVar } from "../lib/theme";
import { Icon } from "./Icon";
import { TickerInput } from "./TickerInput";

export function TickerChips({ tickers, onChange, addable, colored, linkTo, max = 60 }: { tickers: string[]; onChange?: (ts: string[]) => void; addable?: boolean; colored?: boolean; linkTo?: boolean; max?: number }) {
  return (
    <div className="oc-chips">
      {tickers.map((t, i) => (
        <span key={t} className="oc-chip">
          {colored && <span className="oc-chip-dot" style={{ background: seriesVar(i) }} />}
          {linkTo ? <Link to={`/ticker/${t}`} className="num">{t}</Link> : <span className="num">{t}</span>}
          {onChange && (
            <button type="button" aria-label={`Remove ${t}`} onClick={() => onChange(tickers.filter((x) => x !== t))}>
              <Icon name="x" size={12} />
            </button>
          )}
        </span>
      ))}
      {addable && onChange && tickers.length < max && (
        <TickerInput className="oc-chips-input" placeholder="Add…" exclude={tickers} onSelect={(t) => !tickers.includes(t) && onChange([...tickers, t])} />
      )}
    </div>
  );
}
