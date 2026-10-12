"""``signals.overlay`` (design §4.2, D5; INC-01, *The overlay*): one exposure scale
for the desk, from volatility targeting on the roster's combined targets.

    exposure_scale = min(1, vol_target / sigma_hat),   sigma_hat = sqrt(w' Sigma w), annualised

* ``w`` (:func:`combined_targets`): over the roster's cells with a document in the
  signals directory, ``capital_fraction x weight`` from each slug's NEWEST
  document (highest ``sequence``), summed by symbol -- the research service's
  slugs included. A malformed file is skipped and named in the notes. No
  documents yet: ``w`` is empty, ``sigma_hat`` 0, the scale 1, with a note.
* ``Sigma`` (:func:`covariance`): the P3 league's (``cov.league``) rank-1
  estimator for the ``site_etfs`` universe in its newest artifact, fitted on the
  last 252 sessions of the warehouse's daily returns for ``w``'s names (the
  farm's ``prices(warehouse_first=True)`` path); ``lw_constant_corr``
  (Ledoit-Wolf) when there is no artifact or no such row. ANNUALISED: every
  portfolio/covariance.py frame is daily x 252 (its header), and
  ``pca_factor_cov`` (models/cov_league.py, daily) is multiplied by 252 here.
* The diagonal (:func:`replace_diagonal`): each name's variance is replaced by
  the P2 league's (``vol.forecast_league``) rank-1 model by ``rank_qlike`` for
  that ticker -- its ``var_1d_<model>``, a next-day variance in DECIMAL^2 daily
  units, x 252 -- or, where P2 has none, EWMA(0.94) on the same returns
  (risk.core's RiskMetrics recursion, P2's own ``ewma``), as INC-01 says. The
  off-diagonals are rescaled so the estimator's correlations are kept
  (rho_ij sigma_i sigma_j). ``inputs.vol_model`` names the model of the
  largest-weight name; the ``names`` table names every name's source.
* A non-finite forecast RAISES: the job fails and nothing is written, so the
  desk holds its last overlay until it is stale (the designed failure mode).
  So does a materially negative ``w' Sigma w`` (below ``-1e-12 x w' diag(Sigma) w``):
  a covariance that is not positive semi-definite is not a number to clamp to 0.
* Stale bars RAISE too (the stage review's B-I5): returns whose last session
  (``bars_asof``) is more than one session behind the previous NYSE session at
  the run's New York date -- an ingest that failed twice -- write nothing, the
  message naming both dates.
* ``sources`` (B-I4): one row per cell whose document fed ``w`` -- slug,
  sequence, as_of, capital_fraction, the file -- so the run can be re-derived.

Reported beside the number and never used in it (INC-01: Q02 is DESCRIPTIVE
ONLY; P1 computes reference books): the newest ``regime.hmm`` artifact's last
``p_high`` and the core book's 99 % one-day Monte Carlo VaR from the newer of
``risk.mc_atlas`` / ``risk.mc_intraday`` (FHS rows, as products/atlas.py's
reader takes them). Both None when absent.

``sequence`` is the previous ``signals.overlay`` artifact's + 1, and never
below the newest ``overlay-<n>.json`` in the signals directory + 1, so a wiped
ledger cannot replay a sequence the desk already judged. ``as_of`` is the run's
own wall-clock instant, UTC, to the minute; the bars' date is
``reported.bars_asof`` and the artifact's.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..jobs.artifacts import ArtifactSpec
from .contract import overlay_doc, write_atomic
from .roster import ROSTER_PATH, Roster, load_roster

__all__ = ["DB_PATH", "ROSTER_PATH", "combined_targets", "covariance", "diagonal_vols", "exposure_scale",
           "forecast_vol", "replace_diagonal", "run"]

KIND = "signals.overlay"
VOL_KIND = "vol.forecast_league"
COV_KIND = "cov.league"
REGIME_KIND = "regime.hmm"
MC_KINDS = ("risk.mc_atlas", "risk.mc_intraday")
COV_UNIVERSE = "site_etfs"
FALLBACK_ESTIMATOR = "lw_constant_corr"
LOOKBACK = 252
MIN_SESSIONS = 30   # below this the covariance and the EWMA seed are noise: refuse, nothing written
EWMA_LAMBDA = 0.94
TRADING_DAYS = 252
NY = ZoneInfo("America/New_York")
DB_PATH: Path | None = None   # tests point every artifact read at a temp jobs.sqlite
_OVERLAY_FILE = re.compile(r"^overlay-(\d+)\.json$")
OVERLAY_COLUMNS = ["as_of", "sequence", "exposure_scale", "vol_target", "vol_forecast", "vol_model",
                   "cov_estimator", "vol_artifact", "cov_artifact", "bars_asof", "n_names", "regime_p_high",
                   "mc_var_99_1d_core", "path"]
NAMES_COLUMNS = ["symbol", "weight", "cov_vol", "vol_forecast", "diag_source", "p2_asof"]
SOURCES_COLUMNS = ["slug", "sequence", "as_of", "capital_fraction", "path"]
PSD_TOLERANCE = 1e-12


def _now() -> datetime:
    return datetime.now(UTC)


def _db() -> Path:
    from ..jobs.paths import jobs_db_path

    return Path(DB_PATH) if DB_PATH is not None else jobs_db_path()


# ------------------------------------------------------------------- targets
def _newest_doc(signals_dir: Path, slug: str,
                notes: list[str]) -> tuple[int, dict[str, float], str | None, str] | None:
    """The slug's document with the highest ``sequence``, as ``(sequence, {symbol: weight},
    as_of, file name)``; every unreadable file is skipped and named in ``notes``."""
    best: tuple[int, dict[str, float], str | None, str] | None = None
    for p in sorted(signals_dir.glob(f"{slug}-*.json")):
        try:
            doc = json.loads(p.read_text())
            seq = int(doc["sequence"])
            if not isinstance(doc["targets"], list):
                raise TypeError("targets is not a list")
            w: dict[str, float] = {}
            for t in doc["targets"]:
                v = float(t["weight"])
                if not math.isfinite(v):
                    raise ValueError(f"weight {v} for {t['symbol']}")
                w[str(t["symbol"])] = w.get(str(t["symbol"]), 0.0) + v
        except (OSError, ValueError, KeyError, TypeError) as e:
            notes.append(f"skipped {p.name}: unreadable ({e})")
            continue
        if best is None or seq > best[0]:
            as_of = doc.get("as_of")
            best = (seq, w, str(as_of) if as_of is not None else None, p.name)
    return best


def combined_targets(signals_dir: Path, roster: Roster, *, notes: list[str] | None = None,
                     sources: list[dict[str, Any]] | None = None) -> dict[str, float]:
    """``sum over cells of capital_fraction x weight`` from each slug's newest document;
    each document used is appended to ``sources`` (:data:`SOURCES_COLUMNS`)."""
    notes = [] if notes is None else notes
    out: dict[str, float] = {}
    d = Path(signals_dir)
    if not d.is_dir():
        return out
    for cell in roster.cells:
        got = _newest_doc(d, cell.slug, notes)
        if got is None:
            continue
        seq, w, as_of, name = got
        if sources is not None:
            sources.append({"slug": cell.slug, "sequence": seq, "as_of": as_of,
                            "capital_fraction": cell.capital_fraction, "path": name})
        for s, v in w.items():
            out[s] = out.get(s, 0.0) + cell.capital_fraction * v
    return {s: v for s, v in out.items() if v != 0.0}


# ---------------------------------------------------------------- covariance
def covariance(returns: pd.DataFrame, estimator: str) -> pd.DataFrame:
    """The ANNUALISED covariance of daily ``returns`` by the cov league's estimator
    name; ``lw_constant_corr`` for a name it does not know. One name: its sample
    variance x 252 (every estimator needs two; the diagonal is replaced anyway)."""
    from ..models.cov_league import pca_factor_cov
    from ..portfolio.covariance import ESTIMATORS, estimate_covariance

    names = list(returns.columns)
    if len(names) == 1:
        return pd.DataFrame([[float(returns.iloc[:, 0].var(ddof=1)) * TRADING_DAYS]], index=names, columns=names)
    if estimator == "pca_factor":
        m = pca_factor_cov(returns.to_numpy(dtype=float)) * TRADING_DAYS
        return pd.DataFrame(m, index=names, columns=names)
    return estimate_covariance(returns, estimator if estimator in ESTIMATORS else FALLBACK_ESTIMATOR).cov


def replace_diagonal(cov: pd.DataFrame, vol_forecasts: dict[str, float] | None) -> pd.DataFrame:
    """``cov`` with each named variance set to ``vol_forecasts[name]^2`` (annualised
    vols) and its row and column rescaled by ``new sigma / old sigma``, so every
    correlation is kept. A non-finite or negative forecast, or a zero-variance name
    given one, raises ``ValueError``."""
    out = cov.astype(float).copy()
    if not vol_forecasts:
        return out
    k = np.ones(len(out))
    for i, name in enumerate(out.index):
        if name not in vol_forecasts:
            continue
        new, old = float(vol_forecasts[name]), math.sqrt(max(float(out.iloc[i, i]), 0.0))
        if not math.isfinite(new) or new < 0:
            raise ValueError(f"vol forecast for {name} is {new}")
        if old == 0.0:
            raise ValueError(f"{name} has zero variance in the covariance: no correlation to keep")
        k[i] = new / old
    return out * np.outer(k, k)


def forecast_vol(targets: dict[str, float], cov: pd.DataFrame, vol_forecasts: dict[str, float] | None) -> float:
    """``sqrt(w' Sigma w)``, annualised, with ``vol_forecasts`` replacing Sigma's
    diagonal (:func:`replace_diagonal`). 0 for no targets; non-finite raises."""
    names = [s for s, v in targets.items() if v != 0.0]
    if not names:
        return 0.0
    missing = [s for s in names if s not in cov.index]
    if missing:
        raise ValueError(f"no covariance for {missing}")
    sigma = replace_diagonal(cov.loc[names, names], vol_forecasts).to_numpy(dtype=float)
    w = np.array([float(targets[s]) for s in names])
    var = float(w @ sigma @ w)
    if not math.isfinite(var):
        raise ValueError(f"portfolio variance is {var}")
    floor = -PSD_TOLERANCE * float(w @ np.diag(np.diag(sigma)) @ w)
    if var < floor:
        raise ValueError(f"portfolio variance is {var}, materially negative (below {floor}): the covariance "
                         "is not positive semi-definite, no overlay")
    return math.sqrt(max(var, 0.0))


def exposure_scale(vol_target: float, vol_forecast: float) -> float:
    """``min(1, vol_target / vol_forecast)``; 1 for a zero forecast; a non-finite or
    negative forecast raises ``ValueError`` (the job fails, nothing is written)."""
    if not math.isfinite(vol_forecast) or vol_forecast < 0:
        raise ValueError(f"vol forecast is {vol_forecast}: no overlay")
    if not math.isfinite(vol_target) or vol_target <= 0:
        raise ValueError(f"vol target is {vol_target}")
    if vol_forecast == 0.0:
        return 1.0
    return min(1.0, vol_target / vol_forecast)


# ------------------------------------------------------------------ artifacts
def _latest(kind: str, now: datetime, notes: list[str]) -> tuple[dict[str, Any], Path] | None:
    from ..products.io import latest_with_path

    got = latest_with_path(kind, now=now, db_path=_db())
    if got is None:
        return None
    manifest, path, stale = got
    if stale:
        notes.append(f"{kind} artifact {manifest['id']} is stale (used, as the newest there is)")
    return manifest, path


def _cov_winner(now: datetime, notes: list[str]) -> tuple[str, str]:
    from ..products.io import read_table

    got = _latest(COV_KIND, now, notes)
    if got is None:
        notes.append(f"no {COV_KIND} artifact: {FALLBACK_ESTIMATOR} (Ledoit-Wolf)")
        return FALLBACK_ESTIMATOR, "none"
    manifest, path = got
    lg = read_table(path, "league")
    hit = pd.DataFrame() if lg is None else lg[(lg["universe"] == COV_UNIVERSE) & (lg["rank"] == 1)]
    if hit.empty:
        notes.append(f"{COV_KIND} {manifest['id']} has no rank-1 row for {COV_UNIVERSE}: {FALLBACK_ESTIMATOR}")
        return FALLBACK_ESTIMATOR, str(manifest["id"])
    return str(hit.iloc[0]["estimator"]), str(manifest["id"])


def _p2_best(now: datetime, notes: list[str]) -> tuple[dict[str, tuple[str, float, str]], str]:
    """``{ticker: (model, var_1d, asof)}`` for each ticker whose rank-1 model by
    ``rank_qlike`` has a finite positive next-day variance; and the artifact id."""
    from ..products.io import read_table

    got = _latest(VOL_KIND, now, notes)
    if got is None:
        notes.append(f"no {VOL_KIND} artifact: EWMA(0.94) on every name's diagonal")
        return {}, "none"
    manifest, path = got
    lg, fc = read_table(path, "league"), read_table(path, "forecasts")
    out: dict[str, tuple[str, float, str]] = {}
    if lg is None or fc is None or lg.empty or fc.empty:
        return out, str(manifest["id"])
    fc = fc.drop_duplicates("ticker", keep="last").set_index("ticker")
    for ticker, rows in lg.groupby("ticker"):
        best = rows.sort_values(["rank_qlike", "qlike"], kind="mergesort").iloc[0]
        col = f"var_1d_{best['model']}"
        if ticker not in fc.index or col not in fc.columns:
            continue
        v = pd.to_numeric(fc.loc[ticker, col], errors="coerce")
        if pd.notna(v) and math.isfinite(float(v)) and float(v) > 0:
            out[str(ticker)] = (str(best["model"]), float(v), str(fc.loc[ticker].get("asof", "")))
        else:
            notes.append(f"{VOL_KIND} {manifest['id']}: {ticker}'s rank-1 model {best['model']} has var_1d {v}, "
                         "not a positive finite variance: EWMA(0.94) for its diagonal")
    return out, str(manifest["id"])


def _regime_p_high(now: datetime, notes: list[str]) -> tuple[float | None, str | None]:
    from ..products.io import read_table

    got = _latest(REGIME_KIND, now, notes)
    if got is None:
        return None, None
    manifest, path = got
    probs = read_table(path, "probs")
    if probs is None or probs.empty or "p_high" not in probs.columns:
        notes.append(f"reported {REGIME_KIND} {manifest['id']}: no probs table with p_high; reported as null")
        return None, str(manifest["id"])
    v = pd.to_numeric(probs.sort_values("date")["p_high"], errors="coerce").iloc[-1]
    return (float(v) if pd.notna(v) else None), str(manifest["id"])


def _mc_var(now: datetime, notes: list[str]) -> tuple[float | None, str | None]:
    from ..jobs.db import parse_iso
    from ..products.io import read_table

    found = [g for g in (_latest(k, now, notes) for k in MC_KINDS) if g is not None]
    if not found:
        return None, None
    manifest, path = max(found, key=lambda g: parse_iso(g[0]["finished_at"]))
    s = read_table(path, "summary")
    if s is None or s.empty:
        return None, str(manifest["id"])
    hit = s[(s["book"] == "core") & np.isclose(s["alpha"].astype(float), 0.99) & (s["horizon"] == 1)]
    if "method" in hit.columns and (hit["method"] == "fhs").any():
        hit = hit[hit["method"] == "fhs"]
    if hit.empty or pd.isna(hit.iloc[0]["var"]):
        return None, str(manifest["id"])
    return float(hit.iloc[0]["var"]), str(manifest["id"])


def _sequence(signals_dir: Path) -> int:
    from ..products.io import previous_artifact, read_table

    prev = 0
    got = previous_artifact(KIND, db_path=_db())
    if got is not None:
        t = read_table(got[1], "overlay")
        if t is not None and len(t):
            prev = int(t["sequence"].max())
    on_disk = [int(m.group(1)) for p in signals_dir.glob("overlay-*.json") if (m := _OVERLAY_FILE.match(p.name))]
    return max(prev, max(on_disk, default=0)) + 1


# ------------------------------------------------------------------------ run
def bars_refusal(bars_asof: date, today: date) -> str | None:
    """Why returns ending ``bars_asof`` are too old for a run on New York date ``today``:
    more than one session behind the previous NYSE session. None when they are not."""
    from .calendar import previous_session

    prev = previous_session(today)
    floor = previous_session(prev)
    if bars_asof < floor:
        return (f"the bars end {bars_asof}, more than one session behind {prev}, the previous NYSE session "
                f"at this run ({today}): the daily ingest has not run; no overlay")
    return None


def _returns(ctx: Any, names: list[str]) -> pd.DataFrame:
    px = ctx.market.prices(names, warehouse_first=True).data[names].dropna()
    r = px.pct_change().dropna().iloc[-LOOKBACK:]
    if len(r) < MIN_SESSIONS:
        raise ValueError(f"{len(r)} sessions of returns for {names} (< {MIN_SESSIONS}): no overlay")
    return r


def diagonal_vols(returns: pd.DataFrame,
                  p2: dict[str, tuple[str, float, str]]) -> dict[str, tuple[float, str, str | None]]:
    """Each name's annualised vol for Sigma's diagonal, its source and P2's asof:
    ``sqrt(var_1d x 252)`` from P2's best model where there is one, else
    ``sqrt(EWMA(0.94) next-day variance x 252)`` on ``returns`` (risk.core's
    RiskMetrics recursion, seeded with the mean square of the first 30 returns)."""
    from ..risk.core import ewma_variance_path

    out: dict[str, tuple[float, str, str | None]] = {}
    for s in returns.columns:
        if s in p2:
            model, v, asof = p2[s]
            out[s] = (math.sqrt(v * TRADING_DAYS), f"p2:{model}", asof)
        else:
            v = float(ewma_variance_path(returns[s].to_numpy(dtype=float), EWMA_LAMBDA)[-1])
            out[s] = (math.sqrt(v * TRADING_DAYS), "ewma", None)
    return out


def _reported(read: Any, kind: str, now: datetime, notes: list[str]) -> tuple[Any, str | None]:
    """A reported-only read: never stops the number. Any error is ``None`` and a note."""
    try:
        return read(now, notes)
    except Exception as e:  # noqa: BLE001 - a descriptive artifact may not keep the overlay from being written
        notes.append(f"reported {kind}: unreadable ({type(e).__name__}: {e}); reported as null")
        return None, None


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    out_dir = ctx.signals_dir
    if out_dir is None:
        raise RuntimeError("OHCAMEL_QUANT_SIGNALS_DIR is not set: signals.overlay writes only to the signals directory")
    out_dir = Path(out_dir)
    now = _now()
    as_of = now.astimezone(UTC).replace(second=0, microsecond=0)
    roster = load_roster(ROSTER_PATH)
    notes: list[str] = []
    sources: list[dict[str, Any]] = []
    targets = combined_targets(out_dir, roster, notes=notes, sources=sources)
    estimator, cov_artifact = _cov_winner(now, notes)
    p2, vol_artifact = _p2_best(now, notes)
    names = sorted(targets)
    rows: list[dict[str, Any]] = []
    bars_asof: str | None = None
    vol_model = "ewma"
    if not names:
        notes.append("no targets yet: no roster document in the signals directory, exposure_scale 1")
        sigma_hat = 0.0
    else:
        r = _returns(ctx, names)
        stale = bars_refusal(r.index[-1].date(), now.astimezone(NY).date())
        if stale is not None:
            raise ValueError(stale)
        bars_asof = r.index[-1].date().isoformat()
        cov = covariance(r, estimator)
        diag = diagonal_vols(r, p2)
        vols = {s: diag[s][0] for s in names}
        for s in names:
            _, source, p2_asof = diag[s]
            rows.append({"symbol": s, "weight": targets[s], "cov_vol": math.sqrt(max(float(cov.loc[s, s]), 0.0)),
                         "vol_forecast": vols[s], "diag_source": source, "p2_asof": p2_asof})
        sigma_hat = forecast_vol(targets, cov, vols)
        largest = max(names, key=lambda s: (abs(targets[s]), s))
        vol_model = p2[largest][0] if largest in p2 else "ewma"
    scale = exposure_scale(roster.vol_target, sigma_hat)
    p_high, regime_id = _reported(_regime_p_high, REGIME_KIND, now, notes)
    mc_var, mc_id = _reported(_mc_var, "risk.mc_atlas/risk.mc_intraday", now, notes)
    n_sessions = len(r) if names else 0
    sequence = _sequence(out_dir)
    inputs = {"vol_target": roster.vol_target, "vol_forecast": sigma_hat, "vol_model": vol_model,
              "cov_estimator": estimator, "vol_artifact": vol_artifact, "cov_artifact": cov_artifact}
    reported = {"regime_p_high": p_high, "regime_artifact": regime_id, "mc_var_99_1d_core": mc_var,
                "mc_artifact": mc_id, "bars_asof": bars_asof}
    path = out_dir / f"overlay-{sequence}.json"
    write_atomic(overlay_doc(as_of=as_of, sequence=sequence, exposure_scale=scale, inputs=inputs,
                             reported=reported), path)
    row = {"as_of": as_of.strftime("%Y-%m-%dT%H:%M:%SZ"), "sequence": sequence, "exposure_scale": scale,
           **{k: inputs[k] for k in ("vol_target", "vol_forecast", "vol_model", "cov_estimator", "vol_artifact",
                                     "cov_artifact")},
           "bars_asof": bars_asof, "n_names": len(names), "regime_p_high": p_high, "mc_var_99_1d_core": mc_var,
           "path": path.name}
    notes.append(f"exposure_scale {scale:.6f} = min(1, {roster.vol_target:g} / {sigma_hat:.6f}); "
                 f"covariance {estimator} on {n_sessions} sessions, diagonal from P2's best model by QLIKE "
                 "(EWMA(0.94) where P2 has none); regime p_high and core MC VaR are reported, never used")
    return ArtifactSpec(
        tables={"overlay": pd.DataFrame([row], columns=OVERLAY_COLUMNS),
                "names": pd.DataFrame(rows, columns=NAMES_COLUMNS),
                "sources": pd.DataFrame(sources, columns=SOURCES_COLUMNS)},
        data_asof=bars_asof,
        provenance=[{"source": COV_KIND, "artifact": cov_artifact}, {"source": VOL_KIND, "artifact": vol_artifact},
                    {"source": "roster", "path": str(ROSTER_PATH)},
                    {"source": "reported only", "regime.hmm": regime_id, "mc": mc_id}],
        notes=notes)
