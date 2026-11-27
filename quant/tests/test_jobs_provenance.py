"""Task 3.3: what a number was made from -- the canonical frame hash, the recorded reads, the seed rule.

The canonical form is defined in ``ohcamel_quant.jobs.provenance``'s docstring; each test here pins one of
its decisions (equal values hash equal whatever their dtype, unit, column order or row order; a missing value
is missing whether NaN or None; -0.0 is 0.0; a changed value changes the hash).
"""

from __future__ import annotations

import random
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from ohcamel_quant.jobs import provenance as P


def _bars() -> pd.DataFrame:
    return pd.DataFrame({"ticker": ["SPY", "SPY", "QQQ"],
                         "date": pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-01"]),
                         "close": [500.25, 501.5, 440.0], "volume": [1e6, 2e6, 3e6]})


def test_the_hash_is_sha256_hex_and_stable_for_equal_frames():
    h = P.frame_hash(_bars())
    assert len(h) == 64 and int(h, 16) >= 0
    assert P.frame_hash(_bars()) == h


def test_one_changed_value_changes_the_hash():
    b = _bars()
    b.loc[1, "close"] = 501.5000000001
    assert P.frame_hash(b) != P.frame_hash(_bars())
    b = _bars()
    b.loc[2, "ticker"] = "IWM"
    assert P.frame_hash(b) != P.frame_hash(_bars())


def test_column_order_and_row_order_are_not_part_of_the_hash():
    b = _bars()
    assert P.frame_hash(b[["volume", "close", "date", "ticker"]]) == P.frame_hash(b)
    assert P.frame_hash(b.iloc[::-1].reset_index(drop=True)) == P.frame_hash(b)


def test_a_non_default_index_is_data_and_a_default_one_is_not():
    b = _bars()
    assert P.frame_hash(b.set_index("date")) == P.frame_hash(b)               # the index is the date column
    assert P.frame_hash(b.reset_index(drop=True)) == P.frame_hash(b)
    shifted = b.copy()
    shifted.index = [10, 11, 12]                                             # a real index: a column "index"
    assert P.frame_hash(shifted) != P.frame_hash(b)


def test_datetime_units_do_not_matter():
    b = _bars()
    us = b.assign(date=b["date"].astype("datetime64[us]"))
    s = b.assign(date=b["date"].astype("datetime64[s]"))
    assert P.frame_hash(us) == P.frame_hash(b) == P.frame_hash(s)
    as_dates = b.assign(date=[date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 1)])   # DuckDB fetchall's DATE
    assert P.frame_hash(as_dates) == P.frame_hash(b)
    as_dt = b.assign(date=[datetime(2026, 6, 1), datetime(2026, 6, 2), datetime(2026, 6, 1)])
    assert P.frame_hash(as_dt) == P.frame_hash(b)


def test_a_date_past_2262_hashes_without_overflow_in_any_unit():
    far = pd.DataFrame({"d": np.array(["9999-12-31"], dtype="datetime64[s]")})
    assert P.frame_hash(far) == P.frame_hash(pd.DataFrame({"d": [date(9999, 12, 31)]}))
    ns = pd.DataFrame({"d": pd.to_datetime(["2026-06-01 09:30:00.000000001"])})
    assert P.frame_hash(ns) != P.frame_hash(pd.DataFrame({"d": pd.to_datetime(["2026-06-01 09:30:00"])}))


def test_a_tz_aware_time_is_not_the_naive_one():
    b = _bars()
    assert P.frame_hash(b.assign(date=b["date"].dt.tz_localize("UTC"))) != P.frame_hash(b)
    ny = b.assign(date=b["date"].dt.tz_localize("UTC").dt.tz_convert("America/New_York"))
    assert P.frame_hash(ny) == P.frame_hash(b.assign(date=b["date"].dt.tz_localize("UTC")))   # one instant


def test_nan_none_and_pd_na_are_one_missing_value():
    a = pd.DataFrame({"x": [1.0, np.nan], "s": ["a", None]})
    b = pd.DataFrame({"x": pd.array([1.0, None], dtype="Float64"), "s": ["a", np.nan]})
    c = pd.DataFrame({"x": pd.Series([1, None], dtype=object), "s": pd.array(["a", pd.NA], dtype="string")})
    assert P.frame_hash(a) == P.frame_hash(b) == P.frame_hash(c)
    assert P.frame_hash(pd.DataFrame({"x": [np.nan, np.nan]})) == P.frame_hash(pd.DataFrame({"x": [None, None]}))
    assert P.frame_hash(a) != P.frame_hash(pd.DataFrame({"x": [1.0, 0.0], "s": ["a", None]}))


def test_minus_zero_is_zero_and_ints_are_numbers():
    assert P.frame_hash(pd.DataFrame({"x": [-0.0, 1.0]})) == P.frame_hash(pd.DataFrame({"x": [0.0, 1.0]}))
    assert P.frame_hash(pd.DataFrame({"x": [0, 1]})) == P.frame_hash(pd.DataFrame({"x": [0.0, 1.0]}))
    assert P.frame_hash(pd.DataFrame({"x": np.array([0, 1], dtype="int32")})) == \
        P.frame_hash(pd.DataFrame({"x": [0.0, 1.0]}))
    # bool is its own kind: True is not 1.0
    assert P.frame_hash(pd.DataFrame({"x": [True, False]})) != P.frame_hash(pd.DataFrame({"x": [1.0, 0.0]}))


def test_column_names_and_text_are_part_of_the_hash():
    assert P.frame_hash(pd.DataFrame({"x": [1.0]})) != P.frame_hash(pd.DataFrame({"y": [1.0]}))
    # a text cell cannot run into the next one: ("ab", "c") is not ("a", "bc")
    assert P.frame_hash(pd.DataFrame({"a": ["ab"], "b": ["c"]})) != P.frame_hash(pd.DataFrame({"a": ["a"], "b": ["bc"]}))
    assert P.frame_hash(pd.DataFrame({"a": ["1"]})) != P.frame_hash(pd.DataFrame({"a": [1.0]}))


def test_a_series_and_non_tables_hash_canonically():
    s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-06-01", "2026-06-02"]), name="SPY")
    assert P.frame_hash(s) == P.frame_hash(s.to_frame())
    assert P.frame_hash({"b": [1, 2], "a": None}) == P.frame_hash({"a": None, "b": [1, 2]})
    assert P.frame_hash({"a": float("nan")}) == P.frame_hash({"a": None})
    assert P.frame_hash([("SPY", 1.0)]) != P.frame_hash([("SPY", 2.0)])


def test_a_frame_inside_a_dict_is_hashed_whole_not_through_its_repr():
    """Fix round 1: a dict holding a DataFrame was hashed through str(frame), which truncates to its head and tail:
    two 1000-row frames differing in row 500 hashed equal."""
    a = pd.DataFrame({"x": np.arange(1000, dtype=float)})
    b = a.copy()
    b.loc[500, "x"] = -1.0
    assert P.frame_hash({"panel": a}) != P.frame_hash({"panel": b})
    assert P.frame_hash({"s": a["x"]}) != P.frame_hash({"s": b["x"]})
    assert P.frame_hash({"panel": a}) == P.frame_hash({"panel": a.copy()})


def test_an_object_the_hash_cannot_encode_is_refused_not_hashed_by_its_repr():
    class Opaque:
        def __repr__(self) -> str:
            return "Opaque(...)"

    with pytest.raises(TypeError, match="Opaque"):
        P.frame_hash({"x": Opaque()})
    with pytest.raises(TypeError, match="Opaque"):
        P.frame_hash(pd.DataFrame({"x": [Opaque()]}))


def test_duplicate_column_names_are_refused():
    df = pd.DataFrame([[1, 2]], columns=["a", "a"])
    with pytest.raises(ValueError, match="duplicate"):
        P.frame_hash(df)


def test_data_hash_is_the_hash_of_the_sorted_name_and_hash_lines():
    reads = [{"name": "x", "sha256": "b" * 64}, {"name": "y", "sha256": "a" * 64}]
    assert P.data_hash(reads) == P.data_hash(list(reversed(reads)))
    assert P.data_hash(reads) != P.data_hash(reads[:1])
    assert P.data_hash([]) == P.sha256_text("")
    swapped = [{"name": "x", "sha256": "a" * 64}, {"name": "y", "sha256": "b" * 64}]
    assert P.data_hash(swapped) != P.data_hash(reads)                       # two reads cannot trade their data
    assert P.data_hash(reads) == P.sha256_text("x\t" + "b" * 64 + "\n" + "y\t" + "a" * 64)


# --- the recorder ------------------------------------------------------------------------------------------------

def test_the_recorder_catches_an_unseeded_default_rng():
    rec = P.Recorder()
    with rec.active():
        np.random.default_rng().normal()
    assert rec.unseeded and "default_rng" in rec.unseeded[0]
    rec = P.Recorder()
    with rec.active():
        np.random.default_rng(7).normal()
    assert rec.unseeded == []


def test_an_unseeded_generator_that_is_never_drawn_from_is_not_a_draw():
    """arch's MultipleComparison builds a placeholder bootstrap on default_rng() and replaces it with the seeded
    one before any draw (vol.forecast_league's MCS): a generator is judged by whether it was drawn from."""
    rec = P.Recorder()
    with rec.active():
        placeholder = np.random.default_rng()
    assert rec.unseeded == [] and placeholder is not None


def test_the_recorder_catches_numpy_and_python_global_draws():
    rec = P.Recorder()
    with rec.active():
        np.random.normal()
    assert any("numpy.random" in u for u in rec.unseeded)
    rec = P.Recorder()
    with rec.active():
        random.random()
    assert any("random module" in u for u in rec.unseeded)


def test_the_recorder_restores_numpy_default_rng_after_the_run():
    real = np.random.default_rng
    with P.Recorder().active():
        assert np.random.default_rng is not real
    assert np.random.default_rng is real


def test_a_seed_is_recorded_by_name_and_a_missing_one_is_refused():
    rec = P.Recorder()
    assert rec.seed(20_261_006) == 20_261_006 and rec.seed(7, "bootstrap") == 7
    assert rec.seeds == {"seed": 20_261_006, "bootstrap": 7}
    with pytest.raises(P.UnseededDraw, match="no seed"):
        rec.seed(None)
    with pytest.raises(P.UnseededDraw, match="two seeds"):
        rec.seed(8, "bootstrap")


# --- the recording wrappers, and every read a product makes -----------------------------------------------------

def test_the_recording_connection_records_each_fetch_and_flags_a_bypass(tmp_path):
    import duckdb

    from ohcamel_quant.warehouse.fixture_db import build_fixture_warehouse

    wh = build_fixture_warehouse(tmp_path / "w.duckdb")
    rec = P.Recorder()
    con = P.RecordingConnection(duckdb.connect(str(wh), read_only=True), rec)
    rows = con.execute("SELECT ticker, count(*) FROM bars_daily GROUP BY 1 ORDER BY 1").fetchall()
    df = con.execute("SELECT * FROM fred WHERE series = ?", ["DGS10"]).df()
    one = con.execute("SELECT max(date) FROM bars_daily").fetchone()
    assert rows and not df.empty and one[0] is not None
    assert [r["name"].split(" ", 2)[1] for r in rec.reads] == ["SELECT", "SELECT", "SELECT"]
    assert '| ["DGS10"]' in rec.reads[1]["name"] and all(r["source"] == "warehouse" for r in rec.reads)
    assert rec.reads[1]["sha256"] == P.frame_hash(df)
    con.sql("SELECT 1")                                                   # not execute(): not recorded, and said
    assert rec.unrecorded == ["warehouse.sql() bypassed the recorder"]
    con.close()


def test_the_recording_market_records_each_dataset_with_its_sources(market):
    rec = P.Recorder()
    m = P.RecordingMarket(market, rec)
    ds = m.returns(["SPY"])
    assert rec.reads[0]["sha256"] == P.frame_hash(ds.data)
    assert rec.reads[0]["source"].startswith("fixture") and rec.reads[0]["regenerable"]
    assert m.settings is market.settings and rec.unrecorded == []          # attributes pass through


def test_a_vendor_source_is_not_regenerable():
    rec = P.Recorder()
    rec.record("market.quotes", pd.DataFrame({"x": [1.0]}), source="alpaca")
    rec.record("w", pd.DataFrame({"x": [1.0]}), source="warehouse:alpaca,derived")
    assert [r["regenerable"] for r in rec.reads] == [False, True]


def test_an_earlier_artifact_is_recorded_and_a_replay_pins_it(tmp_path):
    from lane_m_harness import publish_artifact

    from ohcamel_quant.products.io import previous_artifact, read_table

    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    aid, path = publish_artifact(db, root, "farm.sweep", {"cells": pd.DataFrame({"x": [1.0]})})
    rec = P.Recorder()
    with rec.active():
        got = previous_artifact("farm.sweep", db_path=db)
        table = read_table(got[1], "cells")
    assert [r["name"] for r in rec.reads] == ["artifact-lookup: farm.sweep params_hash=None with_table=None",
                                             f"artifact: farm.sweep/{aid}/cells"]
    assert rec.reads[0]["artifact"] == f"farm.sweep/{aid}" and rec.reads[1]["sha256"] == P.frame_hash(table)
    later, _ = publish_artifact(db, root, "farm.sweep", {"cells": pd.DataFrame({"x": [2.0]})},
                                finished=datetime(2026, 10, 7, tzinfo=__import__("datetime").UTC))
    replay = P.Recorder(replay={rec.reads[0]["name"]: str(path)})
    with replay.active():
        again = previous_artifact("farm.sweep", db_path=db)
    assert again[0]["id"] == aid and later != aid                          # the pinned one, not the newest
    with P.Recorder(replay={}).active(), pytest.raises(LookupError, match="did not"):
        previous_artifact("farm.sweep", db_path=db)


# Every direct read in a product, a signal producer or a job handler -- a file, a database, the network -- is
# either recorded (the second element says how) or declared in provenance.UNRECORDED_READS for its kind, or is a
# committed file the code SHA covers. A new one fails here until it is one of the three.
DIRECT_READS = {
    ("products/experiment.py", "read_text("): (2, "the frozen config is committed (code SHA); a frozen universe on "
                                                  "the data volume is recorded by load_config (source data_dir)"),
    ("products/data_integrity.py", "read_parquet("): (1, "the committed fixtures, recorded with ctx.read"),
    ("products/io.py", "connect("): (1, "the artifact index: the lookup is recorded"),
    ("products/io.py", "open_ro("): (1, "the artifact index, read-only (book_vols): the lookup is recorded"),
    ("products/io.py", "read_text("): (2, "an earlier artifact's manifest: the lookup is recorded"),
    ("products/io.py", "read_parquet("): (1, "an earlier artifact's table: recorded by read_table"),
    ("products/kronos_shadow.py", "read_csv("): (1, "Kronos's output, recorded with ctx.read (derived:kronos)"),
    ("products/kronos_shadow.py", "subprocess."): (3, "Kronos runs as a subprocess on recorded bars"),
    ("products/factor_screen.py", "read_text("): (1, "the owner's committed factor ledger (code SHA)"),
    ("products/q01.py", "open_ro("): (1, "the freeze CLI, not the job"),
    ("signals/daily.py", "read_text("): (2, "the committed book (code SHA); the last document: UNRECORDED_READS"),
    ("signals/daily.py", "httpx."): (1, "the live engine: UNRECORDED_READS"),
    ("signals/freeze.py", "connect("): (1, "publishes the freeze artifact (a write)"),
    ("signals/overlay.py", "read_text("): (1, "the signal documents: UNRECORDED_READS"),
    ("signals/contract.py", "read_text("): (1, "the committed JSON schema (code SHA)"),
    ("signals/roster.py", "read_text("): (2, "the committed roster and the pre-registration beside it (code SHA)"),
    ("signals/report.py", "httpx."): (2, "the live engine: UNRECORDED_READS"),
    ("signals/report.py", "read_text("): (1, "INC-02's stored decision on the data volume: UNRECORDED_READS"),
    # completion task 3.6: ops.selftest's test-only hang (never scheduled, not submittable through the API): it holds
    # a DuckDB file's writer lock and starts a `sleep`; nothing is read and nothing reaches the artifact
    ("jobs/handlers/selftest.py", "duckdb.connect("): (1, "test-only: holds a writer lock, reads nothing"),
    ("jobs/handlers/selftest.py", "subprocess."): (1, "test-only: a `sleep` child, reads nothing"),
}
PATTERNS = ("read_parquet(", "read_csv(", "read_text(", "read_bytes(", "connect_ro(", "open_ro(", "duckdb.connect(",
            "httpx.", "requests.", "urlopen(", "subprocess.", "connect(")


def test_every_direct_read_in_a_product_is_recorded_or_declared():
    import re

    from ohcamel_quant.config import REPO_ROOT

    pkg = REPO_ROOT / "quant" / "src" / "ohcamel_quant"
    found: dict[tuple[str, str], int] = {}
    for sub in ("products", "signals", "jobs/handlers"):
        for f in sorted((pkg / sub).glob("*.py")):
            rel = str(f.relative_to(pkg))
            for line in f.read_text().splitlines():
                code = line.split("#", 1)[0]
                for pat in PATTERNS:
                    hits = len(re.findall(r"(?<![A-Za-z_.])" + re.escape(pat), code)) if pat == "connect(" else \
                        code.count(pat)
                    if pat == "httpx." and "except" in code:
                        hits = 0                                          # an exception class, not a read
                    if hits:
                        found[(rel, pat)] = found.get((rel, pat), 0) + hits
    undeclared = {k: n for k, n in found.items() if k not in DIRECT_READS}
    assert not undeclared, f"direct reads neither recorded nor declared: {undeclared}"
    changed = {k: (found.get(k, 0), n) for k, (n, _) in DIRECT_READS.items() if found.get(k, 0) != n}
    assert not changed, f"the count of a declared read moved (found, declared): {changed}"
    for kind in ("signals.daily", "signals.overlay", "desk.report"):
        assert kind in P.UNRECORDED_READS


def test_a_vendor_retrys_jitter_is_not_a_draw_from_the_random_module():
    """data/http.py's backoff jitter feeds no number; it draws from its own generator, so an ingest that retries
    a vendor is not refused as an unseeded draw."""
    from ohcamel_quant.data import http

    rec = P.Recorder()
    with rec.active():
        assert 0.0 <= http.backoff_delay(3) <= http.BACKOFF_CAP_S
    assert rec.unseeded == []


def test_a_frozen_universe_on_the_data_volume_is_a_recorded_read(tmp_path, monkeypatch):
    """Fix round 1: load_config lays {data_dir}/experiments/<EXP>/universe.yaml over a config with no universe; that
    file is data the job read, not code, and no export holds it."""
    from ohcamel_quant.products import experiment as X

    (tmp_path / "exps" / "EXP-T").mkdir(parents=True)
    (tmp_path / "exps" / "EXP-T" / "config.yaml").write_text("experiment: EXP-T\nseed: 1\n")
    (tmp_path / "frozen" / "EXP-T").mkdir(parents=True)
    (tmp_path / "frozen" / "EXP-T" / "universe.yaml").write_text("universe: [SPY, TLT]\nuniverse_frozen_on: '2026-10-08'\n")
    monkeypatch.setattr(X, "EXPERIMENTS", tmp_path / "exps")
    monkeypatch.setattr(X, "FROZEN_DIR", tmp_path / "frozen")
    rec = P.Recorder()
    with rec.active():
        cfg = X.load_config("EXP-T")
    assert cfg["universe"] == ["SPY", "TLT"]
    assert [(r["name"], r["source"], r["regenerable"]) for r in rec.reads] == [
        ("data_dir: experiments/EXP-T/universe.yaml", "data_dir", False)]


def _unseeded_in(src: str, name: str) -> list[str]:
    import ast

    bad = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and not node.args and not node.keywords:
            fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if fn in ("default_rng", "RandomState"):
                bad.append(f"{name}:{node.lineno}: {fn}() with no seed")
        if isinstance(node, ast.ImportFrom) and node.module == "numpy.random" and \
                any(a.name == "default_rng" for a in node.names):
            bad.append(f"{name}:{node.lineno}: from numpy.random import default_rng")
    return bad


def test_no_unseeded_generator_is_written_anywhere_in_the_package():
    """Fix round 1: the runtime detector cannot see an RNG bound at import (``from numpy.random import default_rng``)
    or an unseeded ``np.random.RandomState()``; so the source may contain neither, nor a bare ``default_rng()``
    (read from the syntax tree, so a docstring that names one is not a hit)."""
    from ohcamel_quant.config import REPO_ROOT

    assert len(_unseeded_in("import numpy as np\nfrom numpy.random import default_rng\nnp.random.RandomState()\n"
                            "default_rng()\nnp.random.default_rng(7)\n'default_rng()'\n", "probe")) == 3
    bad = []
    for f in sorted((REPO_ROOT / "quant" / "src" / "ohcamel_quant").rglob("*.py")):
        bad += _unseeded_in(f.read_text(), f.name)
    assert not bad, bad
