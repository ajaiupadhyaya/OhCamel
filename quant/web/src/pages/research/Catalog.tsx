/**
 * The strategy catalog: every rule from GET /api/backtest/strategies as one ruled radio table
 * grouped by family (name, source, default universe size, rebalance), then the selected
 * rule's citation and terms. Explanations live in Methodology behind the Note marker.
 */
import { Note } from "../../design";
import { fmtNum } from "../../lib/format";
import { CATEGORY_LABEL, CATEGORY_ORDER, shortCite } from "./info";
import { Readline } from "./shared";
import type { StrategySpec } from "./types";

export function Catalog({ strategies, selected, onSelect }: { strategies: StrategySpec[]; selected: string; onSelect: (key: string) => void }) {
  const groups = [...new Set([...CATEGORY_ORDER, ...strategies.map((s) => s.category)])].map((c) => ({ c, items: strategies.filter((s) => s.category === c) })).filter((g) => g.items.length);
  return (
    <div className="sl-strategies">
      <div className="sl-strat sl-strat-headrow" aria-hidden>
        <span>STRATEGY</span>
        <span className="sl-strat-cite">SOURCE</span>
        <span className="sl-strat-n">N</span>
        <span className="sl-strat-rebal">REBAL</span>
      </div>
      <div role="radiogroup" aria-label="Strategy">
        {groups.map((g) => (
          <div key={g.c} role="presentation">
            <span className="sl-strat-family" aria-hidden>
              {CATEGORY_LABEL[g.c] ?? g.c}
            </span>
            {g.items.map((s) => {
              const active = s.key === selected;
              return (
                <button key={s.key} type="button" role="radio" aria-checked={active} className={`sl-strat ${active ? "active" : ""}`} onClick={() => onSelect(s.key)}>
                  <span className="sl-strat-name">{s.name}</span>
                  <span className="sl-strat-cite">{shortCite(s.citation)}</span>
                  <span className="sl-strat-n">{s.default_tickers.length}</span>
                  <span className="sl-strat-rebal">{s.default_rebalance.toUpperCase()}</span>
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

/** The selected rule: its citation (with the Methodology note) and its terms. */
export function StrategyFoot({ spec }: { spec: StrategySpec }) {
  return (
    <div className="sl-strat-foot">
      <div className="sl-cite">
        {spec.citation}
        <Note n={1} to="strategies" />
      </div>
      <Readline
        items={[
          { k: "FAMILY", v: CATEGORY_LABEL[spec.category] ?? spec.category.toUpperCase() },
          { k: "REBAL", v: spec.default_rebalance.toUpperCase() },
          { k: "GROSS ≤", v: `${fmtNum(spec.default_max_leverage, spec.default_max_leverage % 1 ? 1 : 0)}×` },
          { k: "MIN ASSETS", v: spec.min_assets },
          spec.max_assets != null && { k: "MAX ASSETS", v: spec.max_assets },
          spec.needs_market && { k: "MARKET", v: "BENCH" },
        ]}
      />
    </div>
  );
}
