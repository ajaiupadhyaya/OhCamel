"""EOD Historical Data (EODHD): end-of-day bars and corporate actions, delisted tickers included (completion task 3.8,
spec R5). A paid source behind a switch: nothing calls it unless ``OHCAMEL_DELISTED_SOURCE=eodhd`` and
``EODHD_API_KEY`` are set (config.py), and nothing depends on it until ``tools/accept_delisted.py`` has passed on the
owner's key (warehouse/accept.py).

**What the vendor serves** (its documentation, https://eodhd.com/financial-apis/api-for-historical-data-and-volumes,
read 2026-11-20, and the committed AAPL responses in quant/tests/data):

* ``/api/eod/{CODE}.US?from&to&fmt=json``: one object per session, ``date, open, high, low, close,
  adjusted_close, volume``. ``open..close`` are as traded, never adjusted; ``adjusted_close`` is adjusted for
  splits and dividends; ``volume`` IS split-adjusted (AAPL 2020-08-20: 126,907,188 served, 31,726,797 traded,
  one quarter, before the 2020-08-31 4-for-1). A delisted ticker keeps its history (the page's example is TWTR);
  a RENAMED ticker does not: its whole history moves to the new code (FB.US is empty, META.US has it).
* ``/api/splits/{CODE}.US``: ``date`` (ex-date) and ``split`` as ``"new/old"`` (``"4.000000/1.000000"``).
* ``/api/div/{CODE}.US``: ``date`` (ex-date), ``value`` (split-adjusted) and ``unadjustedValue`` (as paid).

**What this module returns.** :func:`bars` gives RAW OHLCV, the shape of ``bars_raw``: prices as served, volume
divided back by the ratios of every later split (what traded that day). :func:`actions` gives ``corp_actions`` rows:
splits (ratio new per old) and cash dividends as paid on the ex-date (``unadjustedValue``). Both are written by
``ingest.delisted`` with ``source = 'eodhd'``; ``warehouse/adjust.py`` derives the adjusted series from them, and the
committed AAPL responses reproduce EODHD's own ``adjusted_close`` returns to 2e-5 (its figure has 4 decimals).

**Codes.** A warehouse series is its last ticker across renames (``membership_series``): EODHD keeps a
renamed company's history under the new code, so that is the code to ask for. ``BRK.B`` is ``BRK-B.US``. A series
keyed ``TICKER@DATE`` is the EARLIER holder of a ticker another member company took later; EODHD's code for such a
name is not documented, and asking for the bare ticker could return the later company's prices, so it is refused.

**The key.** EODHD takes it only as the ``api_token`` query parameter. It is passed to the request and nowhere else:
not into a provenance record, not into an error (every message is passed through :func:`http.scrub` and the key's
literal value is removed), not into a log line (data/http.py filters httpx's request log).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Settings, get_settings
from . import http
from .base import DataUnavailable, Provenance

SOURCE = "eodhd"
BASE = "https://eodhd.com/api"
ENDPOINTS = {"eod": BASE + "/eod/{code}", "div": BASE + "/div/{code}", "splits": BASE + "/splits/{code}"}
DOCS = "https://eodhd.com/financial-apis/api-for-historical-data-and-volumes"
RAW = ["open", "high", "low", "close", "volume"]


def code_for(series: str) -> str:
    """The EODHD code of a warehouse series (module docstring, Codes)."""
    s = series.strip().upper()
    if "@" in s:
        raise DataUnavailable(f"{s}: the earlier holder of a ticker a later member company reused; EODHD's code for "
                              "it is not known, and the bare ticker could return the later company's prices")
    return s.replace(".", "-") + ".US"


def _key(settings: Settings) -> str:
    if not settings.eodhd_api_key:
        raise DataUnavailable("EODHD_API_KEY is not set: the delisted-names source has no key (docs/runbooks/compute.md, "
                              "Delisted names)")
    return settings.eodhd_api_key


def _scrub(text: str, key: str) -> str:
    return http.scrub(text).replace(key, "***") if key else http.scrub(text)


def fetch(endpoint: str, code: str, params: dict[str, str], settings: Settings | None = None) -> bytes:
    """One response body, as the vendor sent it. Errors carry the vendor's status and words, never the key."""
    s = settings or get_settings()
    key = _key(s)
    url = ENDPOINTS[endpoint].format(code=code)
    try:
        resp = http.get(url, s, params={**params, "fmt": "json", "api_token": key}, source=SOURCE)
    except DataUnavailable as e:
        raise DataUnavailable(_scrub(f"{code} {endpoint}: {e}", key)) from None
    return resp.content


def provenance(endpoint: str, code: str, params: dict[str, str], *, delisted: bool) -> dict[str, Any]:
    """The provenance record of one request: the URL and parameters WITHOUT the key."""
    return Provenance.now(SOURCE, url=ENDPOINTS[endpoint].format(code=code), params=dict(params), code=code,
                          delisted=delisted, docs=DOCS).to_dict()


def _date_index(values: Any) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(values)).astype("datetime64[ns]").rename("date")


def _rows(payload: Any, what: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and payload.get("errors"):
        raise DataUnavailable(f"EODHD {what}: {payload['errors']}")
    if not isinstance(payload, list):
        raise DataUnavailable(f"EODHD {what}: expected a list, got {type(payload).__name__}")
    return payload


def parse_eod(payload: Any) -> pd.DataFrame:
    """``open, high, low, close, adjusted_close, volume`` as served (volume split-adjusted) on a ``date`` index."""
    rows = _rows(payload, "eod")
    cols = ["open", "high", "low", "close", "adjusted_close", "volume"]
    if not rows:
        return pd.DataFrame(columns=cols, index=_date_index([]), dtype=float)
    df = pd.DataFrame(rows)
    missing = sorted(set(["date", *cols]) - set(df.columns))
    if missing:
        raise DataUnavailable(f"EODHD eod: response lacks {', '.join(missing)}")
    out = df[cols].astype(float)
    out.index = _date_index(df["date"])
    if not out.index.is_monotonic_increasing or out.index.has_duplicates:
        out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def parse_splits(payload: Any) -> pd.DataFrame:
    """``ex_date, ratio`` (new shares per old) from ``"new/old"``."""
    rows = []
    for r in _rows(payload, "splits"):
        new, _, old = str(r["split"]).partition("/")
        ratio = float(new) / float(old)
        if not ratio > 0:
            raise DataUnavailable(f"EODHD splits: {r!r} has no positive ratio")
        rows.append((pd.Timestamp(r["date"]), ratio))
    return pd.DataFrame(rows, columns=["ex_date", "ratio"]).astype({"ex_date": "datetime64[ns]", "ratio": float})


def parse_dividends(payload: Any) -> pd.DataFrame:
    """``ex_date, cash``: the dividend as paid on the ex-date (``unadjustedValue``; refused when absent, because
    ``value`` is restated for every later split)."""
    rows = []
    for r in _rows(payload, "div"):
        if r.get("unadjustedValue") is None:
            raise DataUnavailable(f"EODHD div: {r.get('date')} carries no unadjustedValue")
        rows.append((pd.Timestamp(r["date"]), float(r["unadjustedValue"])))
    return pd.DataFrame(rows, columns=["ex_date", "cash"]).astype({"ex_date": "datetime64[ns]", "cash": float})


def raw_bars(served: pd.DataFrame, splits: pd.DataFrame) -> pd.DataFrame:
    """``bars_raw``'s columns: prices as served, volume divided by the ratios of every split dated after the bar."""
    from ..warehouse.adjust import split_factor

    out = served[["open", "high", "low", "close"]].astype(float).copy()
    out["volume"] = served["volume"].astype(float) / split_factor(served.index, splits)
    return out[RAW]


def as_actions(splits: pd.DataFrame, dividends: pd.DataFrame) -> pd.DataFrame:
    """``corp_actions`` rows (``ex_date, kind, ratio, cash``) from the two responses."""
    a = pd.DataFrame({"ex_date": splits["ex_date"], "kind": "split", "ratio": splits["ratio"], "cash": None})
    b = pd.DataFrame({"ex_date": dividends["ex_date"], "kind": "dividend", "ratio": None, "cash": dividends["cash"]})
    out = pd.concat([x for x in (a, b) if len(x)], ignore_index=True) if len(a) or len(b) else \
        pd.DataFrame(columns=["ex_date", "kind", "ratio", "cash"])
    out["ex_date"] = pd.to_datetime(out["ex_date"]).astype("datetime64[ns]")
    return out.sort_values(["ex_date", "kind"], kind="stable").reset_index(drop=True)[["ex_date", "kind", "ratio",
                                                                                         "cash"]]


def splits(ticker: str, settings: Settings | None = None) -> pd.DataFrame:
    return parse_splits(json.loads(fetch("splits", code_for(ticker), {}, settings)))


def bars(ticker: str, start: date, end: date, *, delisted: bool, settings: Settings | None = None,
         split_table: pd.DataFrame | None = None) -> pd.DataFrame:
    """Raw daily OHLCV of ``ticker`` from ``start`` to ``end`` inclusive (module docstring). ``delisted``: the name
    is expected to have stopped trading, so an empty response is reported as the vendor not carrying it under this
    code (it is also recorded in the provenance). ``split_table``: the vendor's splits when already fetched (the
    volume is divided back by them); otherwise they are fetched."""
    code = code_for(ticker)
    params = {"from": start.isoformat(), "to": end.isoformat()}
    served = parse_eod(json.loads(fetch("eod", code, params, settings)))
    if served.empty:
        what = "no prices among its delisted tickers" if delisted else "no prices"
        raise DataUnavailable(f"EODHD has {what} for {code} from {start} to {end}")
    table = split_table if split_table is not None else splits(ticker, settings)
    out = raw_bars(served, table)
    out.attrs["provenance"] = provenance("eod", code, params, delisted=delisted)
    return out


def actions(ticker: str, settings: Settings | None = None, *, split_table: pd.DataFrame | None = None) -> pd.DataFrame:
    """Every split and cash dividend the vendor lists for ``ticker`` (``ex_date, kind, ratio, cash``)."""
    code = code_for(ticker)
    table = split_table if split_table is not None else splits(ticker, settings)
    divs = parse_dividends(json.loads(fetch("div", code, {}, settings)))
    out = as_actions(table, divs)
    out.attrs["provenance"] = provenance("div", code, {}, delisted=False)
    return out


def record_slice(code: str, start: date, end: date, out_dir: Path, *, delisted: bool,
                 last_trading_date: date | None = None, settings: Settings | None = None) -> list[Path]:
    """``python -m ohcamel_quant.warehouse eodhd-slice``: save the eod response for ``[start, end]`` and the full
    split and dividend lists of ``code``, each byte for byte, each with the fixture sidecar (synthetic false, the URL
    and parameters without the key, the instant in UTC, sha256, what was kept). Refuses to save anything that does
    not parse."""
    s = settings or get_settings()
    out: list[Path] = []
    for endpoint, params in (("eod", {"from": start.isoformat(), "to": end.isoformat()}), ("splits", {}), ("div", {})):
        body = fetch(endpoint, code, params, s)
        fetched = datetime.now(UTC).replace(microsecond=0)
        _ = {"eod": parse_eod, "splits": parse_splits, "div": parse_dividends}[endpoint](json.loads(body))
        path = Path(out_dir) / f"eodhd_{endpoint}_{code}_{start}_{end}.json"
        path.write_bytes(body)
        meta = {"source": SOURCE, "synthetic": False, "code": code, "endpoint": endpoint,
                "url": ENDPOINTS[endpoint].format(code=code), "params": {**params, "fmt": "json"},
                "params_note": "the key is not recorded: it is a credential, sent with the request only",
                "fetched_utc": fetched.isoformat().replace("+00:00", "Z"),
                "fetched_note": "the instant the response finished arriving (UTC, this machine's clock)",
                "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                "kept": "the whole response body, unmodified", "delisted": delisted,
                "used_by": "completion task 3.8: the EODHD adapter's tests (tests/test_data_eodhd.py, "
                           "tests/test_warehouse_delisted.py)"}
        if last_trading_date is not None:
            meta["last_trading_date"] = last_trading_date.isoformat()
        path.with_name(path.name + ".meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        out.append(path)
    return out
