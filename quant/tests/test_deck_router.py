"""/api/deck (api/routers/deck.py), offline against the committed real fixtures.

Offline, the quotes are the last committed daily closes and the history ends
on the same bar, so ``today_in_history`` is true and the drawdown does not
double-count the day. The live path is exercised by the vendors' @live tests
and by hand; here the arithmetic is pinned against the same fixtures.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from ohcamel_quant.api.routers import deck
from ohcamel_quant.config import get_settings
from ohcamel_quant.data.base import DataUnavailable
from ohcamel_quant.data.market import Dataset
from ohcamel_quant.deck.clock import Clock

CORE = {"holdings": [{"ticker": "SPY", "weight": 0.4}, {"ticker": "QQQ", "weight": 0.15},
                     {"ticker": "IWM", "weight": 0.05}, {"ticker": "TLT", "weight": 0.15},
                     {"ticker": "IEF", "weight": 0.15}, {"ticker": "GLD", "weight": 0.1}]}


@pytest.fixture(scope="module")
def core(client):
    r = client.post("/api/deck/reading", json=CORE)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_reading_has_every_block_the_page_draws(core):
    for k in ("as_of", "clock", "book", "risk", "marks", "blips", "limits", "unevaluated", "feeds",
              "notes", "provenance"):
        assert k in core
    assert [m["ticker"] for m in core["marks"]] == ["SPY", "QQQ", "IWM", "TLT", "IEF", "GLD"]
    assert core["book"]["today_in_history"] is True
    assert any("Offline mode" in n for n in core["notes"])


def test_the_day_pnl_is_the_weighted_sum_of_the_marks_moves(core):
    day = sum(m["weight"] * m["change_pct"] for m in core["marks"])
    assert core["book"]["day_pnl"] == pytest.approx(day)
    assert core["book"]["day_pnl_usd"] == pytest.approx(day * 1_000_000)
    assert core["book"]["equity_usd"] == pytest.approx(1_000_000 * (1 + day))


def test_live_weights_drift_by_each_move_and_stay_fully_invested(core):
    day = core["book"]["day_pnl"]
    for m in core["marks"]:
        assert m["live_weight"] == pytest.approx(m["weight"] * (1 + m["change_pct"]) / (1 + day))
    assert core["book"]["net"] == pytest.approx(1.0) and core["book"]["cash"] == pytest.approx(0.0, abs=1e-12)


def test_blips_split_var_exactly(core):
    assert sum(b["component_var"] for b in core["blips"]) == pytest.approx(core["risk"]["var"])
    assert sum(b["pct_var"] for b in core["blips"]) == pytest.approx(1.0)
    assert core["risk"]["var_usd"] == pytest.approx(core["risk"]["var"] * 1_000_000)


def test_the_default_limits_are_evaluated_in_the_engine_shape(core):
    names = [x["name"] for x in core["limits"]]
    assert names == ["gross-cap", "net-cap", "name-cap", "var-cap", "es-cap", "dd-cap", "day-loss"]
    for x in core["limits"]:
        assert set(x) >= {"name", "scope", "unit", "observed", "threshold", "excess", "breached", "utilisation"}
        assert x["utilisation"] == pytest.approx(x["observed"] / x["threshold"])
    var = next(x for x in core["limits"] if x["name"] == "var-cap")
    assert var["unit"] == "money" and var["threshold"] == pytest.approx(20_000.0)
    assert var["observed"] == pytest.approx(core["risk"]["var_usd"])
    name = next(x for x in core["limits"] if x["name"] == "name-cap")
    assert name["scope"] == "largest name:SPY"
    assert core["unevaluated"] == []


def test_offline_feeds_say_so(core):
    feeds = {f["key"]: f for f in core["feeds"]}
    assert feeds["quotes"]["state"] == "off" and "offline" in feeds["quotes"]["detail"]
    assert feeds["clock"]["state"] == "off"
    assert feeds["recorder"]["state"] == "off" and feeds["engine"]["state"] == "off"


def test_custom_limits_replace_the_defaults(client):
    body = {**CORE, "limits": [{"name": "tight", "kind": "name", "threshold": 0.1, "ticker": "tlt"},
                               {"name": "missing", "kind": "name", "threshold": 0.1, "ticker": "NVDA"}]}
    d = client.post("/api/deck/reading", json=body).json()
    assert [x["name"] for x in d["limits"]] == ["tight"]
    assert d["limits"][0]["scope"] == "name:TLT" and d["limits"][0]["breached"] is True
    assert d["unevaluated"] == [{"name": "missing", "kind": "name", "reason": "NVDA is not in the book"}]


@pytest.mark.parametrize("limits, fragment", [
    ([{"name": "a", "kind": "gross", "threshold": 1}, {"name": "a", "kind": "net", "threshold": 1}], "unique"),
    ([{"name": "a", "kind": "beta", "threshold": 1}], "kind"),
    ([{"name": "a", "kind": "gross", "threshold": 0}], "threshold"),
])
def test_bad_limits_are_422(client, limits, fragment):
    r = client.post("/api/deck/reading", json={**CORE, "limits": limits})
    assert r.status_code == 422 and fragment in r.text


class HalfQuoted:
    """The real fixture market, but TLT has no quote -- as when a vendor drops one name."""

    def __init__(self, market, drop=("TLT",), fail=False):
        self._m, self._drop, self._fail = market, drop, fail
        self.settings = market.settings

    def returns(self, *a, **k):
        return self._m.returns(*a, **k)

    def quotes(self, tickers):
        if self._fail:
            raise DataUnavailable("yahoo: 429, alpaca: no keys")
        ds = self._m.quotes(tickers)
        df = ds.data.copy()
        for t in self._drop:
            df.loc[t, ["price", "change_pct"]] = float("nan")
        return Dataset(df, ds.provenance)


NOW = datetime(2026, 9, 24, 15, 0, tzinfo=UTC)
OPEN = Clock(True, NOW.isoformat(), "2026-09-25T13:30:00+00:00", "2026-09-24T20:00:00+00:00", "2026-09-24", "rule")


def test_a_missing_quote_leaves_the_day_loss_unevaluated(market):
    d = deck.build_reading(deck.DeckIn(**CORE), HalfQuoted(market), now=NOW, clock=OPEN)
    un = {x["name"]: x["reason"] for x in d["unevaluated"]}
    assert "TLT" in un["day-loss"]
    assert d["book"]["day_pnl"] is None
    tlt = next(m for m in d["marks"] if m["ticker"] == "TLT")
    assert tlt["live_weight"] == pytest.approx(0.15 / (1 + sum(
        m["weight"] * m["change_pct"] for m in d["marks"] if m["ticker"] != "TLT")))
    assert any("No usable quote for TLT" in n for n in d["notes"])


def test_no_quotes_at_all_is_still_a_reading_with_the_lamp_down(market):
    d = deck.build_reading(deck.DeckIn(**CORE), HalfQuoted(market, fail=True), now=NOW, clock=OPEN)
    assert {m["error"] for m in d["marks"]} == {"yahoo: 429, alpaca: no keys"}
    assert d["risk"]["var"] > 0                         # risk at close weights still stands


def test_too_little_history_is_503(client):
    r = client.post("/api/deck/reading", json={**CORE, "start": "2026-05-01", "end": "2026-06-01"})
    assert r.status_code == 503 and "observations" in r.text


def test_the_reference_book_is_the_web_apps_default_portfolio(client):
    """The recorder flies `core`; a first-time visitor's portfolio is
    DEFAULT_PORTFOLIO. If they drift apart the recorded tape is another book."""
    src = (Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "portfolio.tsx").read_text()
    block = src[src.index("export const DEFAULT_PORTFOLIO"):]
    block = block[:block.index("};")]
    web = [(t, float(w)) for t, w in re.findall(r'ticker:\s*"([A-Z.^-]+)",\s*weight:\s*([0-9.]+)', block)]
    books = client.get("/api/deck/books").json()
    core = next(b for b in books["books"] if b["key"] == "core")
    assert [(h["ticker"], h["weight"]) for h in core["holdings"]] == web
    assert core["name"] in block
    assert [x["name"] for x in books["default_limits"]][:2] == ["gross-cap", "net-cap"]
    assert books["recorder"]["enabled"] is False           # offline


def test_the_tape_reads_what_the_recorder_wrote(client, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    store = deck.get_store(get_settings())
    reading = deck.build_reading(deck.book_request("core"), deck.get_market(), now=NOW, clock=OPEN)
    from ohcamel_quant.deck.recorder import compact
    store.record("core", "2026-09-24", NOW, compact(reading))
    t = client.get("/api/deck/tape").json()
    assert t["session"] == "2026-09-24" and t["n"] == 1 and t["sessions"] == ["2026-09-24"]
    assert t["var_usd"] == [pytest.approx(reading["risk"]["var_usd"])]
    assert set(t["utilisation"]) == {x["name"] for x in reading["limits"]}
    assert client.get("/api/deck/tape", params={"session": "2026-01-02"}).json()["n"] == 0


def test_an_unknown_book_is_404_and_an_empty_tape_is_not_an_error(client, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    assert client.get("/api/deck/tape", params={"book": "nope"}).status_code == 404
    t = client.get("/api/deck/tape").json()
    assert t["n"] == 0 and t["notes"]


def test_the_quote_date_of_a_bare_close_is_that_date():
    assert deck._quote_date(pd.Timestamp("2026-06-01")).isoformat() == "2026-06-01"
    # 01:30 UTC on the 25th is 21:30 New York on the 24th.
    assert deck._quote_date(pd.Timestamp("2026-09-25T01:30:00")).isoformat() == "2026-09-24"
