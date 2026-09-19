from datetime import date

from ohcamel_research.contract import check, data_hash
from ohcamel_research.replay import load_bars
from ohcamel_research.signal import emit


def test_emit_is_schema_valid_and_unvalidated():
    doc = emit(
        "ma_crossover",
        {"symbol": "SPY", "fast": 50, "slow": 200},
        date(2020, 12, 31),
        load_bars(),
        1,
    )
    assert check(doc) == []
    assert doc["validation"]["status"] == "unvalidated"
    assert doc["as_of"] == "2020-12-31"
    assert all(t["symbol"] == "SPY" for t in doc["targets"])
    assert sum(abs(t["weight"]) for t in doc["targets"]) <= 1.0


def test_emit_is_point_in_time():
    bars = load_bars()
    as_of = date(2020, 6, 30)
    doc = emit("ma_crossover", {"symbol": "SPY", "fast": 50, "slow": 200}, as_of, bars, 1)
    # The data hash covers exactly the bars on or before as_of: nothing later
    # could have reached the strategy.
    assert doc["data_hash"] == data_hash(bars.loc[bars["date"] <= as_of])
    assert doc["data_hash"] != data_hash(bars)


def test_ma_crossover_is_long_after_a_long_rally_and_flat_after_the_covid_low():
    # Hand-checkable on the fixture: after the 2019 rally the 50 > 200 SMA on
    # SPY, so the strategy is long; on 2020-03-31, after the crash, 50 < 200.
    bars = load_bars()
    params = {"symbol": "SPY", "fast": 50, "slow": 200}
    long = emit("ma_crossover", params, date(2019, 12, 31), bars, 1)
    flat = emit("ma_crossover", params, date(2020, 3, 31), bars, 2)
    assert long["targets"] == [{"symbol": "SPY", "weight": 1.0}]
    assert flat["targets"] == []
