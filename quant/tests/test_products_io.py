"""Lane M, M1: reading earlier artifacts back (jobs.sqlite + artifact directories)."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
from lane_m_harness import T0, publish_artifact

from ohcamel_quant.products.io import latest_with_path, previous_artifact, read_table


def test_previous_artifact_is_the_newest_with_a_manifest(tmp_path):
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    publish_artifact(db, root, "risk.mc_atlas", {"summary": pd.DataFrame({"x": [1]})}, finished=T0)
    newer, path = publish_artifact(db, root, "risk.mc_atlas", {"summary": pd.DataFrame({"x": [2]})},
                                   finished=T0 + timedelta(hours=1))
    m, p = previous_artifact("risk.mc_atlas", db_path=db)
    assert m["id"] == newer and p == path
    assert read_table(p, "summary")["x"].tolist() == [2]
    assert read_table(p, "nope") is None
    (path / "manifest.json").unlink()  # a half-pruned directory is skipped, not served
    assert previous_artifact("risk.mc_atlas", db_path=db)[0]["id"] != newer
    assert previous_artifact("cov.league", db_path=db) is None
    assert previous_artifact("cov.league", db_path=tmp_path / "missing.sqlite") is None


def test_latest_with_path_reports_staleness_from_schedules(tmp_path):
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    publish_artifact(db, root, "ops.selftest", {"t": pd.DataFrame({"x": [1]})}, finished=T0)
    _, _, stale = latest_with_path("ops.selftest", now=T0 + timedelta(hours=1), db_path=db)
    assert stale is False
    _, _, stale = latest_with_path("ops.selftest", now=T0 + timedelta(hours=27), db_path=db)
    assert stale is True  # schedules.yaml: ops.selftest max_age 26h


def test_previous_artifact_with_table_skips_newer_artifacts_that_lack_it(tmp_path):
    """An INSUFFICIENT DATA run (outage, short history) publishes no holdout; the holdout lookup must still find
    the artifact that holds the once-only evaluation (Global Constraint: holdouts are evaluated once)."""
    db, root = tmp_path / "jobs.sqlite", tmp_path / "artifacts"
    scored, path = publish_artifact(db, root, "regime.hmm", {"holdout": pd.DataFrame({"x": [1]})}, finished=T0)
    outage, _ = publish_artifact(db, root, "regime.hmm", {"gates": pd.DataFrame({"gate": ["g"]})},
                                 finished=T0 + timedelta(hours=1))
    assert previous_artifact("regime.hmm", db_path=db)[0]["id"] == outage
    m, p = previous_artifact("regime.hmm", db_path=db, with_table="holdout")
    assert m["id"] == scored and p == path
    assert previous_artifact("regime.hmm", db_path=db, with_table="nope") is None
