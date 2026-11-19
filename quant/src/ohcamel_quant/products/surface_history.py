"""P7 job: ``vol.surface_history`` (nightly, heavy, class M): each unprocessed (underlying, asof) snapshot
-> chain -> SVI surface -> one history row. The history is carried forward from the previous artifact and
only grows; the snapshots themselves cannot be re-fetched (Lane C C6).

What a snapshot's surface is built from (completion task 3.4):

* **Pull.** The 15:45 pull (``pull = '1545'``) when the day has one: closing option quotes are stale and wide.
  With only the 16:20 pull, that one is used and the row (``pull``) and the notes say so.
* **Time.** ``T`` is ACT/365 calendar time from the snapshot's ``quote_ts`` (the vendor's statement of when its
  quotes were taken, data/cboe.py) to the settlement instant -- 16:00 New York on the expiry date, 09:30 for
  AM-settled index roots (options/parity.py; the 30 stored underlyings are PM-settled ETF and equity options).
  Calendar time, as the options router counts it and the Cboe VIX method does: rates and dividend yields are
  calendar rates, and over a weekend it counts the closed days, so ``T`` is never below the business time to the
  same expiry. Rows stored before task 3.4 have no ``quote_ts``: their time is the ingest_log's ``as_of``, the
  payload's own timestamp (UTC, converted), which postdates the 16:20 pull's quotes by up to 20 minutes.
* **Spot.** ``spot`` is the vendor's stated underlying price in the same payload (older rows: the ingest_log's).
  The American IVs use the option-implied level when the front expiry forms one (options/parity.implied_spot:
  Cboe's ``current_price`` is the 16:00 print while option quotes run to 16:15), else the vendor's; the row's
  ``surface_spot`` says which.
* **Discounting.** The Treasury zero curve bootstrapped from the FRED par curve dated the snapshot's ``asof`` --
  or the nearest earlier one within ``macro.curve.MAX_CURVE_LAG_DAYS``, said in the notes, else the snapshot is
  refused into ``errors`` -- read through ``ctx.market`` (recorded) with nothing dated after ``asof``. A
  back-processed snapshot uses the curve of its own date. ``vendor_iv`` is carried for comparison only.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..data.base import DataUnavailable
from ..data.market import OptionChain
from ..jobs.artifacts import ArtifactSpec
from ..macro.curve import MAX_CURVE_LAG_DAYS, zero_curve_on
from ..models.surface_metrics import day_metrics, vrp
from ..options.chain import analyze_chain
from ..options.surface import build_surface

KIND = "vol.surface_history"
DB_PATH: Path | None = None
ERR_COLS = ["underlying", "asof", "error"]
COLS = ["underlying", "asof", "atm_iv_30d", "atm_iv_90d", "term_slope", "rr25_30d", "bf25_30d", "mf_var_30d",
        "vrp", "vrp_covers", "vrp_note", "n_slices", "spot",
        # task 3.4: which pull, the quote time T counts from (New York) and its source, the discount curve's date,
        # and which level the American IVs used ('implied' | 'vendor'). Rows carried from before are blank here.
        "pull", "quote_ts", "quote_ts_source", "curve_date", "surface_spot"]
#: The pull a snapshot's surface prefers, then the fallback (warehouse/ingest/options.py).
PULL_PREFERENCE = ("1545", "1620")
OLD_ROW_SOURCE = "cboe:timestamp via ingest_log (stored before task 3.4; up to 20 minutes after the quotes)"
OPEN_TO_CLOSE_NOTE = ("the HAR forecast behind this VRP was of open-to-close variance only: it overstates the "
                      "premium by the overnight share, so it is not shown")


def _pending(con: Any, done: set[tuple[str, str]]) -> list[tuple[str, str]]:
    rows = con.execute('SELECT DISTINCT underlying, CAST("asof" AS VARCHAR) FROM option_snapshots '
                       'ORDER BY 2, 1').fetchall()
    return [(u, a) for u, a in rows if (u, a) not in done]


def _chain(con: Any, und: str, asof: str) -> tuple[OptionChain, dict[str, Any]]:
    """The snapshot's chain at its quote time, from the preferred pull; and ``{pull, quote_ts_source}``."""
    pulls = {p for (p,) in con.execute('SELECT DISTINCT pull FROM option_snapshots WHERE underlying = ? AND "asof" = ?',
                                       [und, asof]).fetchall()}
    pull = next((p for p in PULL_PREFERENCE if p in pulls), None)
    if pull is None:
        raise ValueError(f"{und} {asof}: no snapshot in the pulls {PULL_PREFERENCE} (found {sorted(pulls)})")
    q = con.execute('SELECT expiry, strike, cp, bid, ask, last, volume, open_interest, vendor_iv, spot, quote_ts, '
                    'quote_ts_source FROM option_snapshots WHERE underlying = ? AND "asof" = ? AND pull = ?',
                    [und, asof, pull]).df()
    spots, stamps = q["spot"].dropna(), q["quote_ts"].dropna()
    if len(spots) and len(stamps):
        spot = float(spots.iloc[0])
        as_of = pd.Timestamp(stamps.iloc[0]).tz_localize("UTC").tz_convert("America/New_York").tz_localize(None)
        source = str(q["quote_ts_source"].dropna().iloc[0]) if q["quote_ts_source"].notna().any() else None
    else:   # a row stored before task 3.4: the spot and the payload's stamp are in its ingest_log row
        log = con.execute("SELECT detail FROM ingest_log WHERE dataset = 'option_snapshots' AND key = ? "
                          "AND data_asof = ? AND status = 'ok' ORDER BY ran_at DESC LIMIT 1", [und, asof]).fetchone()
        if log is None:
            raise ValueError(f"{und} {asof}: no ingest_log row with the snapshot's spot")
        d = json.loads(log[0])
        spot = float(d["spot"])
        as_of = pd.Timestamp(d["as_of"]).tz_localize(None).tz_localize("UTC").tz_convert("America/New_York") \
            .tz_localize(None)
        source = OLD_ROW_SOURCE
    quotes = (q.drop(columns=["spot", "quote_ts", "quote_ts_source"]).rename(columns={"cp": "type"})
              .assign(expiry=lambda x: pd.to_datetime(x["expiry"]), root=und))
    return (OptionChain(underlying=und, spot=spot, as_of=as_of, quotes=quotes, quote_ts_source=source),
            {"pull": pull, "quote_ts_source": source})


def _curve(market: Any, asof: str) -> Any:
    """The zero curve as of the snapshot's date (module docstring); raises DataUnavailable or ValueError."""
    day = pd.Timestamp(asof).date()
    tc = market.treasury_curve(day - timedelta(days=30), day)
    return zero_curve_on(tc.data.dropna(how="all"), pd.Timestamp(day))


def _blank_old_basis(hist: pd.DataFrame) -> pd.DataFrame:
    """Rows carried forward from an earlier artifact keep their numbers, except a VRP not marked as computed
    against a close-to-close forecast (every row published before the league's target included the overnight
    move): that value is blanked, with the reason, so the series has no level break on the deploy date."""
    hist = hist.copy()
    if "vrp_covers" not in hist:
        hist["vrp_covers"] = None
    old = hist["vrp"].notna() & (hist["vrp_covers"] != "close_to_close")
    if old.any():
        hist["vrp"] = hist["vrp"].astype(float).mask(old)
        hist["vrp_note"] = hist["vrp_note"].astype(object).mask(old, OPEN_TO_CLOSE_NOTE)
    return hist.reindex(columns=[*COLS, *[c for c in hist.columns if c not in COLS]])


def _har(prev_league: tuple[dict, Path] | None) -> pd.DataFrame | None:
    from .io import read_table

    return read_table(prev_league[1], "forecasts") if prev_league else None


def run(params: dict[str, Any], ctx: Any) -> ArtifactSpec:
    from .io import previous_artifact, read_table

    prev = previous_artifact(KIND, db_path=DB_PATH)
    hist = read_table(prev[1], "history") if prev else None
    hist = hist if hist is not None else pd.DataFrame(columns=COLS)
    hist = _blank_old_basis(hist)
    done = set(zip(hist["underlying"], hist["asof"], strict=True))
    try:
        todo = _pending(ctx.warehouse, done)
    except DataUnavailable as e:
        todo = []
        notes = [f"warehouse unavailable: {e}"]
    else:
        notes = []
    har = _har(previous_artifact("vol.forecast_league", db_path=DB_PATH))
    rows, errors = [], []
    after_close_only: list[str] = []
    curve_lags: dict[str, str] = {}
    for i, (und, asof) in enumerate(todo):
        ctx.check_cancelled()
        try:
            chain, meta = _chain(ctx.warehouse, und, asof)
            try:
                zc = _curve(ctx.market, asof)
            except (DataUnavailable, ValueError) as e:
                raise DataUnavailable(f"no discount curve for {asof} ({e}); the snapshot is not priced on another "
                                      f"date's curve beyond {MAX_CURVE_LAG_DAYS} days") from e
            ca = analyze_chain(chain, rate_curve=zc.rate)
            surf = build_surface(ca, use_kernels=True)
            m = day_metrics(surf.term.reset_index())
        except (DataUnavailable, ValueError, KeyError, np.linalg.LinAlgError) as e:  # one bad snapshot never sinks the night
            errors.append({"underlying": und, "asof": asof, "error": str(e)})
            continue
        if meta["pull"] != PULL_PREFERENCE[0]:
            after_close_only.append(f"{und} {asof}")
        if zc.lag_days:
            curve_lags[asof] = f"{zc.date.date()} ({zc.lag_days} days before)"
        h = None if har is None else har[(har["ticker"] == und) & (har["asof"] <= asof)]
        if h is None or h.empty or "har_22d" not in h or pd.isna(h["har_22d"].iloc[-1]):
            v, why = float("nan"), "no HAR forecast dated on or before the snapshot (minute bars cover ~50 names)"
        elif "har_covers" not in h or h["har_covers"].iloc[-1] != "close_to_close":
            v, why = float("nan"), OPEN_TO_CLOSE_NOTE
        else:
            v, why = vrp(m["mf_var_30d"], float(h["har_22d"].iloc[-1]), covers=str(h["har_covers"].iloc[-1])), None
        rows.append({"underlying": und, "asof": asof, **m, "vrp": v, "vrp_note": why, "spot": chain.spot,
                     "vrp_covers": "close_to_close" if why is None else None, "pull": meta["pull"],
                     "quote_ts": chain.as_of.isoformat(), "quote_ts_source": meta["quote_ts_source"],
                     "curve_date": str(zc.date.date()), "surface_spot": ca.iv_spot_source})
        ctx.progress((i + 1) / max(len(todo), 1), f"{und} {asof}")
    if after_close_only:
        notes.append(f"{len(after_close_only)} snapshot{'s' if len(after_close_only) != 1 else ''} had only the "
                     "after-close pull (16:20), whose option quotes are stale and wide; used as they are: "
                     + ", ".join(after_close_only[:20]))
    if curve_lags:
        notes.append("no Treasury curve dated the snapshot's own day; the nearest earlier one is used: "
                     + ", ".join(f"{a} -> {d}" for a, d in sorted(curve_lags.items())[:20]))
    if rows:
        new = pd.DataFrame(rows, columns=COLS)
        hist = new if hist.empty else pd.concat([hist, new], ignore_index=True)
    days = hist["asof"].nunique()
    if hist.empty:
        return ArtifactSpec(tables={"history": hist, "errors": pd.DataFrame(errors, columns=ERR_COLS)}, data_asof=None,
                            provenance=[], notes=notes, verdict="INSUFFICIENT DATA",
                            verdict_detail="NO OPTION SNAPSHOTS")
    summary = (hist.groupby("underlying").agg(days=("asof", "nunique"), first=("asof", "min"), last=("asof", "max"))
               .reset_index())
    return ArtifactSpec(
        tables={"history": hist, "summary": summary, "errors": pd.DataFrame(errors, columns=ERR_COLS)},
        data_asof=str(hist["asof"].max()), provenance=[{"source": "warehouse:option_snapshots (Cboe delayed)"}],
        notes=[*notes, "SVI per expiry; ATM in total variance, RR/BF linear in T, no extrapolation; "
                       "model-free variance by the Cboe method",
               "the 15:45 pull is preferred to the 16:20 one; T is ACT/365 calendar time from the quotes' own time "
               "(quote_ts) to 16:00 New York on the expiry; discounting with the Treasury zero curve bootstrapped "
               "from the snapshot date's par yields; rows carried from before 2026-11-19 have no pull or "
               "quote_ts: their T ran from the payload's UTC stamp read as New York time (about 5 hours short) "
               "and they discounted at par yields"],
        verdict="DESCRIPTIVE ONLY",
        verdict_detail=f"{days} DAY{'S' if days != 1 else ''} · {hist['underlying'].nunique()} UNDERLYINGS · MEASUREMENT")
