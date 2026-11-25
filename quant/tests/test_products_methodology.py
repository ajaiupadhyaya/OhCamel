"""Lane M: each product has a Methodology entry (quant/web/src/pages/methodology/models.ts, data only)."""

from __future__ import annotations

import re

from ohcamel_quant.config import REPO_ROOT

WEB = REPO_ROOT / "quant" / "web" / "src" / "pages" / "methodology"
# entry id -> section. Each Lane M task appends its entry here.
ENTRIES = {"p1-mc-atlas": "risk", "p2-vol-league": "options", "p3-cov-league": "portfolio", "p4-strategy-farm": "backtest",
           "p7-surface-history": "options", "p5-exp-q01": "backtest",
           "p6-exp-q02": "macro", "k01-kronos-shadow": "options", "factor-workbench": "factors",
           "beta-league": "portfolio", "fama-macbeth-grs": "factors", "almgren-chriss": "risk",
           "inc02-pooled-test": "backtest", "ssvi-surface-league": "options"}


def test_each_product_has_an_entry_with_known_references():
    models = (WEB / "models.ts").read_text()
    refs = set(re.findall(r"^  ([a-z0-9_]+): \{", (WEB / "references.ts").read_text(), re.M))
    for eid, section in ENTRIES.items():
        m = re.search(rf'id: "{re.escape(eid)}",\s*name: "[^"]+",\s*section: "(\w+)"', models)
        assert m, f"no Methodology entry {eid}"
        assert m.group(1) == section
        block = models[m.start():models.index("\n  },", m.start())]
        keys = re.findall(r'"([a-z0-9_]+)"', re.search(r"refs: \[([^\]]*)\]", block).group(1))
        assert keys and set(keys) <= refs, f"{eid}: unknown refs {set(keys) - refs}"


def test_atlas_entry_says_what_members_get():
    """Members are FHS only at 97.5% and 99%; the entry must not claim a copula reading for them."""
    from ohcamel_quant.products import atlas

    models = (WEB / "models.ts").read_text()
    start = models.index('id: "p1-mc-atlas"')
    summary = re.search(r'summary: "([^"]+)"', models[start:]).group(1)
    assert atlas.MEMBER_METHODS == ("fhs",) and atlas.MEMBER_ALPHAS == (0.975, 0.99)
    assert "universe member is measured alone by filtered historical simulation only" in summary
    assert "97.5 and 99%" in summary and "no copula is run for members" in summary
    assert "every universe member, by" not in summary


def test_vol_league_entry_states_the_target_the_code_computes():
    from ohcamel_quant.products import vol_league

    models = (WEB / "models.ts").read_text()
    start = models.index('id: "p2-vol-league"')
    block = models[start:models.index("\n  },", start)]
    assert "close-to-close variance of the next session" in block
    assert "the squared overnight return O_t / C_{t-1} − 1" in block
    assert "official open to the first minute bar's close" in block and "last bar's close to the official close" in block
    assert "split-adjusted prices only" in block
    assert f"fewer than {vol_league.MIN_PRINTS} IEX prints" in block
    assert "O_t}{C_{t-1}} - 1" in block          # the formula carries the overnight term
    assert "P_{t,1}}{O_t} - 1" in block and "C_t}{P_{t,n}} - 1" in block   # and the two intraday legs
    vrp_block = models[models.index('id: "p7-surface-history"'):]
    p7 = vrp_block[:vrp_block.index("\n  },")]
    assert "close-to-close variance" in p7 and "OLD TARGET" in p7


def _charter_gates() -> dict[str, str]:
    text = (REPO_ROOT / "docs" / "CHARTER.md").read_text(encoding="utf-8")
    table = text[text.index("## The gates"):text.index("## Data")]
    rows = [line.strip().strip("|").split("|") for line in table.splitlines() if line.startswith("| ")]
    return {test.strip(): rule.strip() for test, rule in rows[1:]}


def test_the_charters_gates_table_is_the_verdicts_constants():
    """Completion task 2.5 (spec ruling R3, awaiting the owner): docs/CHARTER.md's gates table states exactly the lines
    models/verdict.py gates by -- a constant moved without the charter, or the charter without the constant, fails."""
    from ohcamel_quant.models import verdict as V

    rows = _charter_gates()
    assert rows["Walk-forward, out-of-sample"] == "holdout positive"
    assert rows["Deflated Sharpe Ratio (multiple-testing corrected)"] == f"≥ {V.DSR_MIN:.2f}"
    assert rows["Probabilistic Sharpe Ratio"] == f"≥ {V.PSR_MIN:.2f}"
    assert rows["Excess: Hansen's SPA test against the universe's equal-weight buy-and-hold"] == (
        f"p-value ≤ {V.SPA_MAX_P:.2f}")
    assert rows["Probability of Backtest Overfitting"] == "reported; high values named in the verdict"
    assert rows["Stationary-block bootstrap, ~1000 resamples"] == "lower 5th percentile > 0"
    assert rows[f"Regime stress across the {len(V.REGIMES)} regimes of `research/config/regimes.yaml`"] == (
        f"positive in ≥ {V.REGIMES_MIN}; fewer than {V.REGIMES_MIN} with out-of-sample data is INSUFFICIENT DATA, "
        "not FAIL")
    levels = " / ".join(f"{b:g}" for b in V.COST_GRID_BPS)
    assert rows[f"Cost sensitivity at {levels} bps"] == "reported at every level"
    assert len(rows) == 8


def test_the_farm_entry_states_the_charters_gates():
    from ohcamel_quant.models import verdict as V

    models = (WEB / "models.ts").read_text()
    start = models.index('id: "p4-strategy-farm"')
    summary = re.search(r'summary: "([^"]+)"', models[start:]).group(1)
    assert f"deflated Sharpe ≥ {V.DSR_MIN:.2f}" in summary and f"PSR ≥ {V.PSR_MIN:.2f}" in summary
    assert f"SPA p ≤ {V.SPA_MAX_P:.2f} against the universe's equal-weight buy-and-hold" in summary
    assert f"positive in {V.REGIMES_MIN} of {len(V.REGIMES)} regimes" in summary
    assert "fewer than three with out-of-sample data is insufficient data" in summary
    assert "0.30" not in summary and "0.70" not in summary and "three of five" not in summary


def test_the_farm_entry_claims_nothing_about_how_many_rows_fail():
    """Fix round 1 (M-1): the entry is data, not the leaderboard; it says what every row carries, never a count."""
    models = (WEB / "models.ts").read_text()
    start = models.index('id: "p4-strategy-farm"')
    summary = re.search(r'summary: "([^"]+)"', models[start:]).group(1)
    assert "Most rows fail" not in summary
    assert "PASS, FAIL, or INSUFFICIENT DATA" in summary and "whole grid and full sweep window" in summary


# --- completion Task 5.3: every product's card names its fill, cost model and gates as the code has them -----------
def _entry(eid: str) -> str:
    models = (WEB / "models.ts").read_text()
    start = models.index(f'id: "{eid}"')
    return models[start:models.index("\n  },", start)]


def test_the_farm_card_names_its_fill_gross_cost_model_and_ledger_as_the_code_has_them():
    from ohcamel_quant.backtest import costs as cost_file
    from ohcamel_quant.models import trial_ledger
    from ohcamel_quant.products import farm

    card = _entry("p4-strategy-farm")
    m = cost_file.load()
    a, b, c = (m.tiers_bps[k] for k in sorted(m.tiers_bps))
    assert farm.FILL == "open" and farm.EXECUTION_LAG == 1
    assert f"at gross {farm.MAX_GROSS:g}" in card or f"scored at gross {farm.MAX_GROSS:g}" in card
    assert "decided at a close and filled at the next session's open" in card
    assert f"({a:g}, {b:g} or {c:g} bp one way" in card, "the half-spread tiers moved without the card"
    assert f"{m.borrow_bps:g} bp a year borrow on shorts" in card
    assert f"{m.financing_spread_bps:g} bp a year over the T-bill rate on gross above 1" in card
    assert "0, 1, 3 and 6 times every half-spread" in card and m.sweep == (0.0, 1.0, 3.0, 6.0)
    assert f"{m.participation:.0%} of {m.adv_window}-session median dollar volume" in card
    assert "provisional" in card and "measured" in card
    assert "append-only trial ledger" in card and trial_ledger.LEDGER_NAME.endswith(".jsonl")
    assert "capacity" in card and "turnover" in card
    assert "spec ruling R3, awaiting the owner" in card, "the gates are the spec's default until the owner answers"


def test_the_costs_file_is_the_one_the_cards_say_it_is():
    from ohcamel_quant.backtest import costs as cost_file

    assert cost_file.COSTS_REL == "research/config/costs.yaml" and "research/config/costs.yaml" in _entry("p4-strategy-farm")


def test_the_regimes_the_farm_card_counts_are_the_regime_file():
    from ohcamel_quant.models import verdict as V

    assert f"positive in {V.REGIMES_MIN} of {len(V.REGIMES)} regimes (research/config/regimes.yaml" in _entry("p4-strategy-farm")


def test_the_surface_league_card_names_all_three_models_and_how_heston_is_priced():
    from ohcamel_quant.options import heston

    card = _entry("ssvi-surface-league")
    assert "Heston" in card and "SSVI" in card and "SVI" in card and "COS" in card
    assert f"{heston.LEAGUE_N:,} terms" in card and "Black-76" in card
    assert "never assumed zero" in card and "DESCRIPTIVE ONLY" in card


def test_the_covariance_card_counts_the_estimators_the_code_runs():
    from ohcamel_quant.models import cov_league

    words = {7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven"}
    card = _entry("p3-cov-league")
    assert words[len(cov_league.ESTIMATORS)] + " covariance estimators" in card
    assert f"{cov_league.HOLD} sessions" in card and "no costs" in card


def test_the_beta_card_names_the_leagues_windows_horizon_and_lag():
    from ohcamel_quant.models import beta_league as B

    card = _entry("beta-league")
    assert "over " + ", ".join(str(w) for w in B.WINDOWS[:-1]) + f" and {B.WINDOWS[-1]} sessions" in card
    assert f"next {B.HORIZON} sessions" in card and f"lag is {B.HAC_LAG}" in card
    assert f"{B.KALMAN_WINDOW:,} sessions" in card and f"every {B.REFIT_EVERY}" in card and "Holm" in card


def test_the_pooled_test_card_names_the_horizon_the_level_and_that_nothing_is_approved():
    from ohcamel_quant.signals import pooled

    card = _entry("inc02-pooled-test")
    assert f"over {pooled.SESSIONS} sessions" in card
    assert f"one-sided {pooled.ALPHA:.0%}" in card and f"at least {pooled.PSR_MIN:.2f}" in card
    assert "NOT STARTED" in card and "draft awaiting the owner" in card
    assert "never a return, an equity, a position or a size" in card


def test_a_methodology_card_never_says_an_unanswered_ruling_is_answered():
    for eid in ENTRIES:
        card = _entry(eid)
        assert "approved by the owner" not in card.lower() or eid in {"p5-exp-q01", "p6-exp-q02", "k01-kronos-shadow"}, eid
