/**
 * The strategy catalog rail: one editorial card per rule from the literature, grouped by
 * family. The selected card opens up to the full explanation and citation.
 */
import { Icon } from "../../components/Icon";
import { CATEGORY_LABEL, CATEGORY_ORDER, shortCite } from "./info";
import type { StrategySpec } from "./types";

export function Catalog({
  strategies,
  selected,
  onSelect,
  universeLabel,
}: {
  strategies: StrategySpec[];
  selected: string;
  onSelect: (key: string) => void;
  universeLabel: (tickers: string[]) => string | null;
}) {
  const groups = [
    ...new Set([...CATEGORY_ORDER, ...strategies.map((s) => s.category)]),
  ]
    .map((c) => ({ c, items: strategies.filter((s) => s.category === c) }))
    .filter((g) => g.items.length);
  return (
    <nav className="sl-catalog" aria-label="Strategy catalog">
      <div className="sl-catalog-head">
        <div className="eyebrow">Catalog</div>
        <div className="sl-catalog-title display">
          {strategies.length} rules from the literature
        </div>
        <p className="subtle small">
          Each is implemented exactly as published, on daily adjusted closes,
          and runs through the same cost-aware engine.
        </p>
      </div>
      {groups.map((g) => (
        <div key={g.c} className="sl-catalog-group">
          <div className="sl-catalog-cat">{CATEGORY_LABEL[g.c] ?? g.c}</div>
          {g.items.map((s) => {
            const active = s.key === selected;
            const uni = universeLabel(s.default_tickers);
            return (
              <button
                key={s.key}
                type="button"
                className={`sl-card ${active ? "active" : ""}`}
                aria-pressed={active}
                onClick={() => onSelect(s.key)}
              >
                <span className="sl-card-name">{s.name}</span>
                <span className="sl-card-text clamp">{s.explanation}</span>
                <span className="sl-card-cite">
                  <Icon name="book" size={12} /> {shortCite(s.citation)}
                </span>
                <span className="sl-card-uni">
                  {uni && <span className="sl-card-uni-label">{uni}</span>}
                  <span className="num">
                    {s.default_tickers.slice(0, 6).join(" ")}
                    {s.default_tickers.length > 6
                      ? ` +${s.default_tickers.length - 6}`
                      : ""}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
