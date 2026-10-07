"""Lane M, M2: risk.mc_atlas and risk.mc_intraday on fixture data; the deck's mc_var."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from lane_m_harness import publish_artifact

from ohcamel_quant.api.routers import deck
from ohcamel_quant.deck.clock import rule_clock
from ohcamel_quant.jobs.artifacts import ArtifactSpec
from ohcamel_quant.jobs.context import JobContext
from ohcamel_quant.products import atlas


def ctx(market, threads=1):
    return JobContext(job_id="t", params={}, threads=threads, progress_fn=lambda f, m: None,
                      cancelled_fn=lambda: False, _market=market)


def test_atlas_artifact_on_fixtures(market):
    spec = atlas.run({"n_paths": 20_000, "members": False}, ctx(market))
    assert isinstance(spec, ArtifactSpec) and spec.verdict == "DESCRIPTIVE ONLY"
    s = spec.tables["summary"]
    assert set(s["book"]) == set(deck.BOOKS)
    assert len(s) == len(deck.BOOKS) * 2 * 2 * 3  # methods x horizons x alphas
    assert (s["var_usd"] == s["var"] * s["notional"]).all()
    e = spec.tables["euler"]
    core = e[(e["book"] == "core") & (e["alpha"] == 0.99)]
    es = s[(s["book"] == "core") & (s["method"] == "fhs") & (s["horizon"] == 1) & (s["alpha"] == 0.99)]["es"].iloc[0]
    assert core["es_contribution"].sum() == pytest.approx(es, rel=1e-12)
    assert spec.tables["members"].empty and spec.data_asof == "2026-06-01"  # the fixtures' last session
    assert any("fixture" in str(p.get("source")) for p in spec.provenance)


def test_members_without_history_get_a_reason_not_a_number(market):
    spec = atlas.run({"n_paths": 5_000, "member_paths": 5_000, "members": ["SPY", "NOPE"]}, ctx(market))
    m = spec.tables["members"].set_index("ticker")
    assert (m.loc["SPY", "var"] > 0).all() and len(m.loc[["SPY"]]) == 4  # 2 horizons x 2 alphas
    assert pd.isna(m.loc["NOPE", "var"]) and m.loc["NOPE", "reason"]


def test_intraday_and_the_deck_field(market, tmp_path, monkeypatch):
    spec = atlas.run_intraday({"n_paths": 20_000}, ctx(market))
    s = spec.tables["summary"]
    assert set(s["method"]) == {"fhs", "copula_t"} and (s["alpha"] == 0.99).all()
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    now = datetime.now(UTC)
    publish_artifact(db, root, "risk.mc_intraday", spec.tables, verdict="DESCRIPTIVE ONLY", finished=now)
    weights = {h["ticker"]: h["weight"] for h in deck.BOOKS["core"]["holdings"]}
    got = atlas.intraday_for(weights, now=now, db_path=db)
    row = s[(s["book"] == "core") & (s["method"] == "fhs")].iloc[0]
    assert got["book"] == "core" and got["var_usd"] == pytest.approx(row["var_usd"]) and got["stale"] is False
    assert got["horizon_days"] == 1 and got["method"] == "fhs" and got["as_of"]  # Lane F's mcVarOf needs as_of
    assert atlas.intraday_for({"SPY": 1.0}, now=now, db_path=db) is None  # not a reference book
    assert atlas.intraday_for(weights, now=now + timedelta(hours=2), db_path=db)["stale"] is True  # max_age 35m
    monkeypatch.setattr(atlas, "DEFAULT_DB", db)
    reading = deck.build_reading(deck.book_request("core"), market, now=now, clock=rule_clock(now))
    assert reading["mc_var"]["var_usd"] == pytest.approx(row["var_usd"])


def test_the_deck_never_fails_on_the_batch_tier(market, monkeypatch):
    monkeypatch.setattr(atlas, "intraday_for", lambda *a, **k: 1 / 0)
    now = datetime.now(UTC)
    reading = deck.build_reading(deck.book_request("core"), market, now=now, clock=rule_clock(now))
    assert reading.get("mc_var") is None
