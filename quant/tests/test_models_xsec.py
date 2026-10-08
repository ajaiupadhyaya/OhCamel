"""Lane M, M6: EXP-Q01 pipeline pieces by hand (pre-registration + I-Q01-1..9)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.models.features import Panel
from ohcamel_quant.models.xsec import (
    DIVIDEND_ADJUSTED_SOURCES,
    UNADJUSTED_SOURCES,
    composite_score,
    daily_portfolio,
    freeze_universe,
    ic_table,
    label_returns,
    quantile_weights,
    window_keys,
)

IDX = pd.bdate_range("2026-01-26", periods=10)  # Mon 26 Jan .. Fri 6 Feb 2026; Jan 30 is the January month-end


def _panel(opens: dict[str, list[float]], factor: float = 1.0) -> Panel:
    o = pd.DataFrame(opens, index=IDX[: len(next(iter(opens.values())))])
    c = o.copy()
    return Panel(open=o, close=c, adj_close=c * factor, volume=o * 0 + 1e6)


def test_q01_labels_start_at_the_next_open():
    """Decision 2026-01-30 (Fri); entry = open of Mon 2 Feb; exit = open after the next month-end.
    With only two decisions, the first label is open(exit) / open(entry) - 1; the decision-day open is irrelevant."""
    opens = {"A": [10, 10, 10, 10, 99.0, 20, 21, 22, 23, 24]}
    p = _panel(opens)
    dec = pd.DatetimeIndex([pd.Timestamp("2026-01-30"), pd.Timestamp("2026-02-04")])
    ret, entry, exit_ = label_returns(p, dec)
    assert entry.tolist() == [5, 8] and exit_[0] == 8
    assert ret.loc["2026-01-30", "A"] == pytest.approx(23 / 20 - 1)  # 99.0 (decision-day open) never enters
    assert np.isnan(ret.loc["2026-02-04", "A"])                      # no later decision: no exit, no label


def test_labels_are_total_return_on_the_adjusted_basis():
    p = _panel({"A": [10.0] * 10}, factor=0.5)  # adj_close = close / 2 everywhere: adj_open = open / 2
    dec = pd.DatetimeIndex([pd.Timestamp("2026-01-30"), pd.Timestamp("2026-02-04")])
    assert label_returns(p, dec)[0].iloc[0, 0] == pytest.approx(0.0)  # a constant factor cancels


def test_quantile_weights_by_hand():
    s = pd.Series([5.0, 4.0, 3.0, 2.0, 1.0], index=pd.MultiIndex.from_product(
        [[pd.Timestamp("2026-01-30")], list("ABCDE")], names=["date", "ticker"]))
    w = quantile_weights(s, 0.2).iloc[0]
    assert w.to_dict() == {"A": 1.0, "B": 0.0, "C": 0.0, "D": 0.0, "E": -1.0}  # floor(0.2 x 5) = 1 a side


def test_composite_signs_and_ranks():
    idx = pd.MultiIndex.from_product([[pd.Timestamp("2026-01-30")], ["A", "B"]], names=["date", "ticker"])
    x = pd.DataFrame({"mom_12_1": [0.2, 0.1], "rev_1m": [0.0, 0.1]}, index=idx)
    sc = composite_score(x, {"mom_12_1": 1, "rev_1m": -1})
    # pct ranks: mom A=1, B=0.5 -> centred +0.25 / -0.25; rev A=0.5, B=1 -> signed -(-0.25)=+0.25 / -0.25.
    assert sc.loc[(pd.Timestamp("2026-01-30"), "A")] == pytest.approx(0.25)
    assert sc.loc[(pd.Timestamp("2026-01-30"), "B")] == pytest.approx(-0.25)


def test_daily_portfolio_by_hand():
    """Long A, short B from the open after the decision. Opens: A 100,100,110,121,121; B 100,100,100,90,90.
    Day 1: g = 0.10; turnover 2 from cash; net = 1.10 x (1 - 0.0005 x 2) - 1 = 0.0989.
    Drift: w_A = 1.1/1.1 = 1, w_B = -1 x 1.0/1.1 = -0.909091. Day 2: g = 0.10 + 0.909091 x 0.10 = 0.190909."""
    idx = IDX[:5]
    ao = pd.DataFrame({"A": [100, 100, 110, 121, 121.0], "B": [100, 100, 100, 90, 90.0]}, index=idx)
    w = pd.DataFrame({"A": [1.0], "B": [-1.0]}, index=[idx[0]])
    out = daily_portfolio(w, ao, cost_bps=5.0)
    assert out.index.tolist() == list(idx[1:4])
    assert out["turnover"].tolist() == [2.0, 0.0, 0.0]
    assert out["net"].iloc[0] == pytest.approx(0.0989)
    assert out["gross"].iloc[1] == pytest.approx(0.1 + (1.0 / 1.1) * 0.1)
    assert out["gross"].iloc[2] == pytest.approx(0.0)


def test_rank_ic_of_a_perfect_ranking_is_one():
    idx = pd.MultiIndex.from_product([[pd.Timestamp("2026-01-30")], list("ABCD")], names=["date", "ticker"])
    pred = pd.Series([4.0, 3.0, 2.0, 1.0], index=idx)
    samples = pd.DataFrame({"ret": [0.04, 0.03, 0.01, -0.02]}, index=idx)
    t = ic_table(pred, samples)
    assert t["rank_ic"].iloc[0] == pytest.approx(1.0)


def _cov(dates: pd.DatetimeIndex, *segments: tuple[str, str]) -> dict:
    """Coverage with per-bar sources: ``segments`` = (source, first date it wrote), in date order."""
    src = np.empty(len(dates), dtype=object)
    for name, first in segments:
        src[dates >= pd.Timestamp(first)] = name
    return {"dates": dates, "source": src}


def test_freeze_excludes_unadjusted_and_late_etfs():
    spy = pd.bdate_range("2007-01-02", "2026-09-30")
    cov = {
        "XLK": _cov(spy, ("yahoo", "2007-01-02"), ("alpaca", "2016-01-04")),
        "LATE": _cov(spy[spy >= "2010-01-04"], ("alpaca", "2010-01-04")),
        "STOOQ": _cov(spy, ("stooq", "2007-01-02")),
        "MIXED": _cov(spy, ("stooq", "2007-01-02"), ("alpaca", "2016-01-04")),   # early Stooq stitched to Alpaca
        "GAPPY": _cov(spy.delete(range(3000, 3007)), ("yahoo", "2007-01-02")),
    }
    # Stooq only before the panel start (2006-12-01) is never read: it does not exclude.
    old = pd.bdate_range("2005-01-03", "2026-09-30")
    cov["OLDSTOOQ"] = _cov(old, ("stooq", "2005-01-03"), ("yahoo", "2006-12-01"))
    members, excluded = freeze_universe(cov, spy, pd.Timestamp("2008-01-02"), pd.Timestamp("2026-09-30"),
                                        adjusted_from=pd.Timestamp("2006-12-01"))
    assert members == ["OLDSTOOQ", "XLK"]
    assert "first bar" in excluded["LATE"] and "gap of 7" in excluded["GAPPY"]
    assert "dividend-adjusted" in excluded["STOOQ"] and "stooq" in excluded["STOOQ"]
    assert "stooq" in excluded["MIXED"]  # a partly unadjusted history is caught, not just a Stooq-only one


def test_freeze_keeps_a_fund_that_pays_no_distributions():
    """GLD from an adjusting vendor: adj_close == close every day, and that is correct, not unadjusted."""
    spy = pd.bdate_range("2007-01-02", "2026-09-30")
    members, excluded = freeze_universe({"GLD": _cov(spy, ("yahoo", "2007-01-02"), ("alpaca", "2016-01-04"))},
                                        spy, pd.Timestamp("2008-01-02"), pd.Timestamp("2026-09-30"))
    assert members == ["GLD"] and excluded == {}


def test_freeze_treats_fixture_and_unknown_sources_as_unadjusted():
    spy = pd.bdate_range("2007-01-02", "2026-09-30")
    cov = {"FIX": _cov(spy, ("fixture:yahoo", "2007-01-02")), "UNK": _cov(spy, ("unknown", "2007-01-02"))}
    members, excluded = freeze_universe(cov, spy, pd.Timestamp("2008-01-02"), pd.Timestamp("2026-09-30"))
    assert members == [] and "fixture:yahoo" in excluded["FIX"] and "unknown" in excluded["UNK"]


def test_every_price_provider_is_classified():
    from ohcamel_quant.data import prices

    assert set(prices.PROVIDERS) == DIVIDEND_ADJUSTED_SOURCES | UNADJUSTED_SOURCES
    assert not DIVIDEND_ADJUSTED_SOURCES & UNADJUSTED_SOURCES


def test_window_keys_are_label_entry_sessions():
    """I-Q01-1: the 2021-11-30 decision's label accrues in December (selection); the 2021-12-31 decision's
    starts at the 2022-01-03 open (holdout). Keys are entry sessions, never decision dates."""
    sessions = pd.bdate_range("2021-09-30", "2022-02-28")
    dec = pd.DatetimeIndex(["2021-10-29", "2021-11-30", "2021-12-31", "2022-01-31", "2022-02-28"])
    pos = sessions.get_indexer(dec)
    t1 = np.r_[pos[1:] + 1, len(sessions) - 1]
    idx = pd.MultiIndex.from_product([dec, ["A"]], names=["date", "ticker"])
    samples = pd.DataFrame({"t0": pos, "t1": t1}, index=idx)
    k = window_keys(samples, sessions)["key"]
    assert k[pd.Timestamp("2021-11-30")] == pd.Timestamp("2021-12-01")
    assert k[pd.Timestamp("2021-12-31")] == pd.Timestamp("2022-01-03")
    assert k[pd.Timestamp("2022-02-28")] == pd.Timestamp("2022-03-01")  # newest: no next session, unlabelled
    sel_end, hold = pd.Timestamp("2021-12-31"), pd.Timestamp("2022-01-01")
    in_sel = k[k <= sel_end].index
    # Every selection label's last return day (the session before its exit) is in 2021.
    assert (sessions[samples.loc[in_sel, "t1"].to_numpy() - 1] <= sel_end).all()
    assert k[k >= hold].index.min() == pd.Timestamp("2021-12-31")
