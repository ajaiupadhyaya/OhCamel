"""/api/deck -- the Flight Deck: one book as live cockpit instruments.

A reading marks the book to the latest quotes (Alpaca snapshots when keys
exist, Yahoo otherwise, the last committed closes offline), drifts its weights
by the day's moves, and evaluates the viewer's limits against the result:

* live weights ``w'_i = w_i (1 + r_i) / (1 + sum_j w_j r_j)``, cash at 0 %;
* 1-day parametric VaR/ES at ``alpha`` from the EWMA covariance (RiskMetrics,
  lambda 0.94) at the live weights, with the Euler split by holding; historical
  VaR/ES of the close-weight series as a cross-check;
* day P&L ``sum_i w_i r_i``; drawdown of the constant-weight wealth path with
  today's move appended when it is not already a bar in the history.

The history and its covariance are cached for 30 minutes per (tickers, window,
lambda); only the quotes move between readings, and those are cached 60 s by
the data layer. The flight recorder (``deck/recorder.py``) calls
:func:`build_reading` for the reference book, so the recorded tape and the page
are one computation.

Endpoints: ``POST /deck/reading``, ``GET /deck/books``, ``GET /deck/tape``.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from ...config import Settings, get_settings
from ...data.base import DataUnavailable, Provenance
from ...data.market import MarketData, get_market
from ...deck import limits as lm
from ...deck import live
from ...deck.clock import NY, Clock, session_clock
from ...deck.recorder import Recorder, Store, tape_columns
from ...risk.core import covariance, data_notes, portfolio_returns
from ...risk.decomposition import historical_var_es
from ..models import PortfolioIn
from ..serialize import clean

log = logging.getLogger(__name__)

router = APIRouter(prefix="/deck", tags=["deck"])
Market = Annotated[MarketData, Depends(get_market)]

BOOKS: dict[str, dict[str, Any]] = {
    k: v for k, v in json.loads((Path(__file__).resolve().parents[2] / "deck" / "books.json").read_text()).items()
    if not k.startswith("_")
}
HISTORY_TTL_S = 1800.0
QUOTE_STALE_OPEN_S = 300.0
HISTORY_STALE_DAYS = 4
MIN_OBS = 60


# ------------------------------------------------------------------ requests
class LimitIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    kind: Literal["gross", "net", "name", "var", "es", "drawdown", "day_loss"]
    # DECIMAL fraction: of equity for weights and drawdown, of notional for var/es/day_loss.
    threshold: float = Field(gt=0, le=100)
    ticker: str | None = Field(default=None, max_length=12)

    @field_validator("threshold")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("threshold must be finite")
        return v

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.strip().upper() or None if v else None

    def to_limit(self) -> lm.Limit:
        return lm.Limit(self.name, self.kind, self.threshold, self.ticker if self.kind == "name" else None)


class DeckIn(PortfolioIn):
    limits: list[LimitIn] | None = Field(default=None, max_length=24)
    alpha: float = Field(default=0.95, ge=0.5, lt=1.0)
    ewma_lambda: float = Field(default=0.94, gt=0.5, lt=1.0)

    @model_validator(mode="after")
    def _unique_names(self) -> DeckIn:
        if self.limits:
            names = [x.name for x in self.limits]
            dup = sorted({n for n in names if names.count(n) > 1})
            if dup:
                raise ValueError(f"limit names must be unique: {', '.join(dup)}")
        return self


def book_request(key: str) -> DeckIn:
    b = BOOKS[key]
    return DeckIn(holdings=b["holdings"], benchmark=b["benchmark"], notional=b["notional"])


# ------------------------------------------------------------------ history cache
_HIST: dict[tuple, tuple[float, pd.DataFrame, np.ndarray, list[dict[str, Any]]]] = {}
_HIST_LOCK = threading.Lock()


def _history(body: DeckIn, market: MarketData) -> tuple[pd.DataFrame, np.ndarray, list[dict[str, Any]]]:
    key = (tuple(body.weights), body.start, body.end, body.ewma_lambda, id(market))
    now = time.monotonic()
    with _HIST_LOCK:
        hit = _HIST.get(key)
        if hit and now - hit[0] < HISTORY_TTL_S:
            return hit[1], hit[2], hit[3]
    ds = market.returns(list(body.weights), body.start, body.end)
    rets = ds.data.dropna(how="any")
    if len(rets) < MIN_OBS:
        raise DataUnavailable(f"only {len(rets)} common return observations for {', '.join(body.weights)}")
    _, cov = covariance(rets, "ewma", body.ewma_lambda)
    prov = ds.provenance_dicts()
    with _HIST_LOCK:
        _HIST[key] = (now, rets, cov, prov)
        if len(_HIST) > 64:
            _HIST.pop(next(iter(_HIST)))
    return rets, cov, prov


# ------------------------------------------------------------------ the reading
def _quote_date(as_of: pd.Timestamp) -> date:
    """The New York session date of a quote. A bare date (a daily close, as
    offline) is that date; a UTC instant is converted."""
    ts = pd.Timestamp(as_of)
    if ts.tzinfo is None and ts == ts.normalize():
        return ts.date()
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(NY).date()


def _finite(x: Any) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _marks(tickers: list[str], market: MarketData, now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """One row per holding, in book order. Returns (rows, provenance, failure)."""
    try:
        ds = market.quotes(tickers)
    except DataUnavailable as e:
        return [{"ticker": t, "price": None, "prev_close": None, "change_pct": None, "as_of": None,
                 "age_s": None, "source": None, "error": str(e)} for t in tickers], [], str(e)
    df = ds.data
    rows = []
    for t in tickers:
        r = df.loc[t] if t in df.index else None

        def get(k: str, r: pd.Series | None = r) -> Any:
            return r[k] if r is not None and k in r.index else None

        as_of = get("as_of")
        ts = None if as_of is None or pd.isna(as_of) else pd.Timestamp(as_of)
        age = None
        if ts is not None:
            aware = ts.tz_localize("UTC") if ts.tzinfo is None else ts
            age = max(0.0, (now - aware.to_pydatetime()).total_seconds())
        err = get("error")
        rows.append({
            "ticker": t, "price": _finite(get("price")), "prev_close": _finite(get("prev_close")),
            "change_pct": _finite(get("change_pct")), "as_of": ts, "age_s": age,
            "source": get("source"), "error": err if isinstance(err, str) and err else None,
        })
    return rows, ds.provenance_dicts(), None


def build_reading(body: DeckIn, market: MarketData, settings: Settings | None = None,
                  now: datetime | None = None, clock: Clock | None = None) -> dict[str, Any]:
    settings = settings or market.settings
    now = now or datetime.now(UTC)
    clock = clock or session_clock(settings, now)
    weights = body.weights
    bad = [t for t, x in weights.items() if not math.isfinite(x)]
    if bad:
        raise ValueError(f"weight must be a finite number for: {', '.join(bad)}")
    if body.start is not None and body.end is not None and body.end <= body.start:
        raise ValueError(f"end ({body.end}) must be after start ({body.start})")

    rets, cov, hist_prov = _history(body, market)
    tickers = list(weights)
    marks, quote_prov, quote_failure = _marks(tickers, market, now)
    moves = {m["ticker"]: (None if m["error"] else m["change_pct"]) for m in marks}
    lw, day, missing = live.live_weights(weights, moves)

    notes = data_notes(hist_prov)
    reasons: dict[str, str] = {}
    if settings.offline:
        notes.append("Offline mode: marks are the last committed daily closes, not live prices.")
    else:
        notes.append(f"Quotes are cached for {settings.ttl_intraday_quote_s} s, so marks move about once a minute.")
    if missing:
        notes.append(f"No usable quote for {', '.join(missing)}: held at the previous close in the live weights.")
        reasons["day_loss"] = f"no usable quote for {', '.join(missing)}, so the day's P&L is incomplete"

    # Today's move is appended to the drawdown path only if it is not already a bar.
    last_bar = rets.index[-1].date()
    quote_days = [_quote_date(m["as_of"]) for m in marks if m["as_of"] is not None]
    today_in_history = bool(quote_days) and max(quote_days) <= last_bar
    rp_close = portfolio_returns(rets, weights)
    dd: float | None
    try:
        dd = live.drawdown(rp_close, None if today_in_history or missing else day)
    except ValueError as e:
        dd, reasons["drawdown"] = None, str(e)
    if missing and not today_in_history:
        dd = None
        reasons["drawdown"] = f"today's move is incomplete (no quote for {', '.join(missing)})"

    risk = live.risk_at(lw, list(rets.columns), cov, body.alpha)
    hvar, hes = historical_var_es(rp_close.to_numpy(), body.alpha)
    facts = lm.Facts(notional=body.notional, live_weights=lw, var=risk["var"], es=risk["es"],
                     drawdown=dd, day_pnl=None if missing else day, reasons=reasons)
    limits = [x.to_limit() for x in body.limits] if body.limits is not None else list(lm.DEFAULT_LIMITS)
    evaluated, unevaluated = lm.evaluate(limits, facts)

    n = body.notional
    by_ticker = {m["ticker"]: m for m in marks}
    for m in marks:
        m["weight"] = weights[m["ticker"]]
        m["live_weight"] = lw[m["ticker"]]
    blips = [{
        "ticker": t, "live_weight": lw[t], "change_pct": by_ticker[t]["change_pct"],
        "component_var": risk["component"][t], "component_var_usd": risk["component"][t] * n,
        "pct_var": risk["pct"][t],
    } for t in tickers]

    feeds = _feeds(settings, clock, marks, quote_failure, last_bar)
    return clean({
        "as_of": now.isoformat(), "source": "portfolio",
        "clock": clock.to_dict(),
        "book": {
            "notional": n, "equity_usd": n * (1.0 + day), "day_pnl": None if missing else day,
            "day_pnl_usd": None if missing else day * n,
            "gross": sum(abs(x) for x in lw.values()), "net": sum(lw.values()), "cash": 1.0 - sum(lw.values()),
            "observations": len(rets), "start": rets.index[0], "end": rets.index[-1],
            "today_in_history": today_in_history,
        },
        "risk": {
            "alpha": body.alpha, "var": risk["var"], "es": risk["es"],
            "var_usd": risk["var"] * n, "es_usd": risk["es"] * n,
            "hist_var": hvar, "hist_es": hes, "hist_var_usd": hvar * n, "hist_es_usd": hes * n,
            "model": "parametric, EWMA covariance, live weights, 1 day, zero mean",
            "ewma_lambda": body.ewma_lambda,
        },
        "marks": marks, "blips": blips,
        "limits": evaluated, "unevaluated": unevaluated,
        "feeds": feeds,
        "notes": notes,
        "provenance": hist_prov + quote_prov,
    })


def _feeds(settings: Settings, clock: Clock, marks: list[dict[str, Any]], quote_failure: str | None,
           last_bar: date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    out.append({"key": "clock", "label": "Session clock",
                "state": "ok" if clock.source == "alpaca" else "off",
                "detail": ("Alpaca clock: " if clock.source == "alpaca" else "") +
                          ("open" if clock.is_open else "closed") + (f"; {clock.note}" if clock.note else ""),
                "age_s": None})

    got = [m for m in marks if m["price"] is not None and not m["error"]]
    ages = [m["age_s"] for m in got if m["age_s"] is not None]
    oldest = max(ages) if ages else None
    if settings.offline:
        state, detail = "off", "offline: the last committed daily closes"
    elif not got:
        state, detail = "down", quote_failure or "no holding could be quoted"
    elif len(got) < len(marks):
        state, detail = "stale", f"{len(marks) - len(got)} of {len(marks)} holdings unquoted"
    elif clock.is_open and oldest is not None and oldest > QUOTE_STALE_OPEN_S:
        state, detail = "stale", f"oldest mark {oldest:.0f} s old during the session"
    else:
        state, detail = "ok", f"{len(got)} of {len(marks)} holdings quoted"
    out.append({"key": "quotes", "label": "Quotes", "state": state, "detail": detail, "age_s": oldest})

    for src in sorted({str(m["source"]) for m in got if m["source"]}):
        n = sum(1 for m in got if m["source"] == src)
        src_ages = [m["age_s"] for m in got if m["source"] == src and m["age_s"] is not None]
        out.append({"key": f"source:{src}", "label": src, "state": "ok",
                    "detail": f"{n} mark{'s' if n != 1 else ''}", "age_s": max(src_ages) if src_ages else None})

    lag = (date.fromisoformat(clock.session) - last_bar).days
    out.append({"key": "history", "label": "Daily history",
                "state": "ok" if lag <= HISTORY_STALE_DAYS else "stale",
                "detail": f"last bar {last_bar.isoformat()}", "age_s": None})

    rec = recorder_status(settings)
    rec_state = "off" if not rec["enabled"] else ("down" if not rec["running"] or rec["last_error"] else "ok")
    rec_detail = ("disabled" if not rec["enabled"] else
                  rec["last_error"] or (f"last write {rec['last_write']}" if rec["last_write"] else "no readings yet"))
    out.append({"key": "recorder", "label": "Flight recorder", "state": rec_state, "detail": rec_detail,
                "age_s": None})

    configured = bool((settings.engine_url or "").strip())
    out.append({"key": "engine", "label": "OCaml engine bridge", "state": "ok" if configured else "off",
                "detail": "configured (read on the engine source)" if configured else "not configured on this server",
                "age_s": None})
    return out


# ------------------------------------------------------------------ the recorder
_recorder: Recorder | None = None
_store: Store | None = None
_store_lock = threading.Lock()


def get_store(settings: Settings) -> Store:
    global _store
    path = Path(settings.data_dir) / "deck" / "recorder.sqlite"
    with _store_lock:
        if _store is None or _store.path != path:
            _store = Store(path)
        return _store


def start_recorder(settings: Settings | None = None, market: MarketData | None = None) -> Recorder | None:
    """Start the flight recorder (once per process). No-op offline or when
    ``OHCAMEL_QUANT_RECORDER=0``."""
    global _recorder
    settings = settings or get_settings()
    if settings.offline or not settings.recorder:
        return None
    if _recorder is not None and _recorder.running:
        return _recorder
    m = market or get_market()

    def compute() -> dict[str, dict[str, Any]]:
        return {key: build_reading(book_request(key), m, settings) for key in BOOKS}

    _recorder = Recorder(get_store(settings), compute, lambda: session_clock(settings),
                         interval_s=settings.recorder_interval_s, keep_sessions=settings.recorder_keep_sessions)
    _recorder.start()
    return _recorder


def stop_recorder() -> None:
    if _recorder is not None:
        _recorder.stop()


def recorder_status(settings: Settings) -> dict[str, Any]:
    enabled = settings.recorder and not settings.offline
    st: dict[str, Any] = {
        "enabled": enabled, "running": bool(_recorder and _recorder.running),
        "interval_s": settings.recorder_interval_s, "keep_sessions": settings.recorder_keep_sessions,
        "db": "deck/recorder.sqlite", "last_tick": _recorder.last_tick if _recorder else None,
        "last_error": _recorder.last_error if _recorder else None,
        "rows": 0, "sessions": 0, "last_write": None,
    }
    path = Path(settings.data_dir) / "deck" / "recorder.sqlite"
    if path.exists():
        st.update(get_store(settings).stats())
    return st


# ------------------------------------------------------------------ endpoints
@router.post("/reading")
def reading(body: DeckIn, market: Market) -> dict[str, Any]:
    """The book marked to live quotes, its limits evaluated, and the feeds behind it."""
    return build_reading(body, market)


@router.get("/books")
def books() -> dict[str, Any]:
    """The reference books the flight recorder flies, the default limits, and the recorder's status."""
    settings = get_settings()
    return clean({
        "books": [{"key": k, **v} for k, v in BOOKS.items()],
        "default_limits": [x.to_dict() for x in lm.DEFAULT_LIMITS],
        "recorder": recorder_status(settings),
        "provenance": [],
        "notes": ["Limits are policy, not market data: these are editable defaults."],
    })


@router.get("/tape")
def tape(
    book: Annotated[str, Query(max_length=32)] = "core",
    session: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
) -> dict[str, Any]:
    """A recorded session of a reference book, column-major (``session`` defaults to the newest)."""
    if book not in BOOKS:
        raise HTTPException(404, f"unknown book {book!r}; one of {sorted(BOOKS)}")
    settings = get_settings()
    path = Path(settings.data_dir) / "deck" / "recorder.sqlite"
    notes: list[str] = []
    sessions: list[str] = []
    rows: list = []
    if path.exists():
        store = get_store(settings)
        sessions = store.sessions(book)
        chosen = session or (sessions[0] if sessions else None)
        if chosen:
            rows = store.tape(book, chosen)
        session = chosen
    if not rows:
        notes.append("Nothing recorded for this session yet: the recorder writes one reading a minute "
                     "while the US session is open." if recorder_status(settings)["enabled"] else
                     "The flight recorder is not enabled on this server.")
    prov = [Provenance.now("ohcamel-deck-recorder", book=book, session=session).to_dict()] if rows else []
    return clean({"book": book, "session": session, "sessions": sessions, **tape_columns(rows),
                  "notes": notes, "provenance": prov})
