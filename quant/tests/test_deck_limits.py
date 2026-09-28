"""The Flight Deck's limits (deck/limits.py): each kind, its unit, and the
unevaluated path. Values are derived by hand beside each assertion."""

from __future__ import annotations

import pytest

from ohcamel_quant.deck.limits import DEFAULT_LIMITS, Facts, Limit, evaluate

W = {"SPY": 0.6, "TLT": 0.5, "GLD": -0.2}  # gross 1.3, net 0.9


def facts(**kw) -> Facts:
    base = dict(notional=1_000_000.0, live_weights=W, var=0.012, es=0.018, drawdown=0.05, day_pnl=-0.004)
    base.update(kw)
    return Facts(**base)


def one(limit: Limit, f: Facts | None = None) -> dict:
    done, skipped = evaluate([limit], f or facts())
    assert not skipped
    return done[0]


def test_gross_and_net_are_fractions_of_equity():
    g = one(Limit("g", "gross", 1.5))
    assert g["observed"] == pytest.approx(1.3)            # 0.6 + 0.5 + 0.2
    assert g["utilisation"] == pytest.approx(1.3 / 1.5)
    assert g["excess"] == pytest.approx(-0.2)
    assert (g["unit"], g["scope"], g["breached"]) == ("fraction", "portfolio", False)
    n = one(Limit("n", "net", 0.8))
    assert n["observed"] == pytest.approx(0.9)            # 0.6 + 0.5 - 0.2
    assert n["breached"] is True and n["excess"] == pytest.approx(0.1)


def test_name_limit_takes_the_largest_or_the_named_holding():
    big = one(Limit("n", "name", 0.45))
    assert big["scope"] == "largest name:SPY" and big["observed"] == pytest.approx(0.6)
    short = one(Limit("n", "name", 0.45, ticker="gld"))
    assert short["scope"] == "name:GLD" and short["observed"] == pytest.approx(0.2)   # |-0.2|


def test_a_named_holding_not_in_the_book_is_unevaluated_not_zero():
    done, skipped = evaluate([Limit("n", "name", 0.3, ticker="QQQ")], facts())
    assert done == [] and skipped == [{"name": "n", "kind": "name", "reason": "QQQ is not in the book"}]


def test_money_limits_are_evaluated_in_dollars():
    v = one(Limit("v", "var", 0.02))
    assert (v["unit"], v["observed"], v["threshold"]) == ("money", pytest.approx(12_000.0), pytest.approx(20_000.0))
    assert v["utilisation"] == pytest.approx(0.6)
    e = one(Limit("e", "es", 0.015))
    assert e["observed"] == pytest.approx(18_000.0) and e["breached"] is True
    d = one(Limit("d", "day_loss", 0.01))
    assert d["observed"] == pytest.approx(4_000.0)        # a 0.4 % loss on $1m


def test_a_gain_is_no_day_loss():
    d = one(Limit("d", "day_loss", 0.01), facts(day_pnl=0.003))
    assert d["observed"] == 0.0 and d["utilisation"] == 0.0


def test_drawdown_is_a_fraction():
    d = one(Limit("d", "drawdown", 0.1))
    assert (d["unit"], d["observed"], d["utilisation"]) == ("fraction", 0.05, pytest.approx(0.5))


def test_a_missing_input_is_unevaluated_with_its_reason():
    f = facts(var=None, day_pnl=None, reasons={"var": "no covariance", "day_loss": "no quote for TLT"})
    done, skipped = evaluate([Limit("v", "var", 0.02), Limit("d", "day_loss", 0.01), Limit("g", "gross", 2)], f)
    assert [x["name"] for x in done] == ["g"]
    assert skipped == [{"name": "v", "kind": "var", "reason": "no covariance"},
                       {"name": "d", "kind": "day_loss", "reason": "no quote for TLT"}]


def test_bad_limits_are_refused():
    with pytest.raises(ValueError, match="unknown limit kind"):
        evaluate([Limit("x", "beta", 1.0)], facts())
    with pytest.raises(ValueError, match="positive"):
        evaluate([Limit("x", "gross", 0.0)], facts())


def test_the_defaults_cover_every_kind_once_with_unique_names():
    assert sorted(x.kind for x in DEFAULT_LIMITS) == sorted(
        ["gross", "net", "name", "var", "es", "drawdown", "day_loss"])
    assert len({x.name for x in DEFAULT_LIMITS}) == len(DEFAULT_LIMITS)
