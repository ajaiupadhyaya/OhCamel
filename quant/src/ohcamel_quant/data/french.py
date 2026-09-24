"""Kenneth R. French Data Library: daily Fama-French factors.

Source: https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
(zipped CSVs under ``/ftp/``). Factor definitions:

* ``Mkt-RF``, ``SMB``, ``HML`` -- Fama, E. F. & French, K. R. (1993), "Common
  risk factors in the returns on stocks and bonds", *Journal of Financial
  Economics* 33(1), 3-56.
* ``RMW``, ``CMA`` (and 2x3-sort ``SMB``) -- Fama & French (2015), "A
  five-factor asset pricing model", *JFE* 116(1), 1-22.
* ``Mom`` (UMD) -- Carhart, M. M. (1997), "On persistence in mutual fund
  performance", *Journal of Finance* 52(1), 57-82; after Jegadeesh & Titman
  (1993).
* ``RF`` -- one-month Treasury bill return (Ibbotson Associates).

File format: a free-text preamble, a header row starting with a comma
(``,Mkt-RF,SMB,HML,RF``), data rows ``YYYYMMDD, v1, v2, ...`` in PERCENT,
then a blank line and further tables (annual) or a copyright footer. Missing
values are ``-99.99`` or ``-999``. The parser skips the preamble, stops at the
first non-date row, converts percent to decimals and strips padded column
names (``'Mom   '`` -> ``'Mom'``).
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from ..config import Settings
from . import http
from .base import DataUnavailable, Provenance
from .store import get_store

if TYPE_CHECKING:
    from .market import Dataset

FAMILY = "french"
BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FILES = {
    "ff3": "F-F_Research_Data_Factors_daily",
    "ff5": "F-F_Research_Data_5_Factors_2x3_daily",
    "mom": "F-F_Momentum_Factor_daily",
}
FACTOR_ORDER = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom", "RF"]
MISSING = (-99.99, -999.0)

# Portfolio files also carry tables that are NOT percent returns ("Number of
# Firms in Portfolios", "Average Firm Size" [$ millions], "Sum of BE / Sum of
# ME", "Value Weighted Average of BE/ME" / "of OP" / "of Investment"): those
# keep their published units.
NON_RETURN_TITLE = re.compile(r"number of firms|average firm size|\bsum of\b|weighted average of", re.I)

_ROW = re.compile(r"^\s*(\d{8}|\d{6}|\d{4})\s*,")


def _parse_index(tokens: list[str]) -> pd.DatetimeIndex:
    n = len(tokens[0])
    if n == 8:
        return pd.DatetimeIndex(pd.to_datetime(tokens, format="%Y%m%d"))
    if n == 6:  # monthly: label by month end
        return pd.DatetimeIndex(pd.to_datetime(tokens, format="%Y%m")) + pd.offsets.MonthEnd(0)
    return pd.DatetimeIndex(pd.to_datetime(tokens, format="%Y")) + pd.offsets.YearEnd(0)


def parse_french_tables(text: str, percent: bool = True) -> list[tuple[str, pd.DataFrame]]:
    """All tables in a French CSV as ``[(title, frame)]`` in file order.

    ``title`` is the nearest free-text line above the header ("" if none).
    Values are converted from percent to decimals when ``percent``, except
    for tables whose title marks them as non-return data (firm counts, average
    firm size, BE/ME ratios), which keep their published units.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    tables: list[tuple[str, pd.DataFrame]] = []
    i, n = 0, len(lines)
    while i < n:
        if not _ROW.match(lines[i]):
            i += 1
            continue
        # header = previous non-blank line; title = the non-blank line above it
        h = i - 1
        while h >= 0 and not lines[h].strip():
            h -= 1
        if h < 0 or _ROW.match(lines[h]):
            raise DataUnavailable("french: data rows without a header row")
        header = [c.strip() for c in lines[h].split(",")]
        t = h - 1
        while t >= 0 and not lines[t].strip():
            t -= 1
        title = lines[t].strip() if t >= 0 and not _ROW.match(lines[t]) and "," not in lines[t] else ""
        width = len(lines[i].split(",")[0].strip())
        idx_tokens: list[str] = []
        rows: list[list[float]] = []
        while i < n and _ROW.match(lines[i]):
            parts = [p.strip() for p in lines[i].split(",")]
            if len(parts[0]) != width:
                break
            idx_tokens.append(parts[0])
            rows.append([float(p) if p else np.nan for p in parts[1:]])
            i += 1
        cols = [c for c in header[1:]]
        ncol = max(len(r) for r in rows)
        cols = (cols + [f"col{k}" for k in range(len(cols), ncol)])[:ncol]
        arr = np.array([r + [np.nan] * (ncol - len(r)) for r in rows], dtype=float)
        for m in MISSING:
            arr[np.isclose(arr, m)] = np.nan
        if percent and not french_units_raw(title):
            arr = arr / 100.0
        df = pd.DataFrame(arr, index=_parse_index(idx_tokens), columns=cols)
        df.index.name = "date"
        tables.append((title, df.sort_index()))
    if not tables:
        raise DataUnavailable("french: no data table found in file")
    return tables


def french_units_raw(title: str) -> bool:
    """True when a table (by title) is not a percent-return table."""
    return bool(NON_RETURN_TITLE.search(title or ""))


def parse_french_csv(text: str, percent: bool = True) -> pd.DataFrame:
    """The first table of a French CSV (the daily/monthly returns)."""
    return parse_french_tables(text, percent)[0][1]


def _read_zip(content: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names = [m for m in z.namelist() if m.lower().endswith((".csv", ".txt"))]
            if not names:
                raise DataUnavailable("french: zip holds no CSV")
            return z.read(names[0]).decode("latin-1")
    except zipfile.BadZipFile as e:
        raise DataUnavailable(f"french: not a zip file ({e})") from e


def fetch_dataset(name: str, settings: Settings, table: int = 0) -> Dataset:
    """Any daily/monthly dataset of the library by file stem, e.g.
    ``F-F_Research_Data_Factors_daily`` or ``49_Industry_Portfolios_daily``
    (``table`` selects e.g. value- vs equal-weighted blocks). Decimals.
    Cached for ``Settings.ttl_factors_s`` (weekly); stale copy on failure."""
    from .market import Dataset

    url = f"{BASE}{name}_CSV.zip"

    def fetch() -> tuple[pd.DataFrame, Provenance]:
        text = _read_zip(http.get_bytes(url, settings, source="ken-french"))
        tables = parse_french_tables(text)
        if table >= len(tables):
            raise DataUnavailable(f"french: {name} has {len(tables)} tables, not {table + 1}")
        title, df = tables[table]
        units = ("as published (not a return table)" if french_units_raw(title)
                 else "decimal (source: percent)")
        return df, Provenance.now(
            "ken-french", url=url, file=name, table=title or f"table {table}",
            first=str(df.index[0].date()), last=str(df.index[-1].date()), units=units,
        )

    df, prov = get_store(settings).fetch_or_stale(
        FAMILY, f"{name}#{table}", settings.ttl_factors_s, fetch, offline=settings.offline
    )
    return Dataset(df, [prov])


def fetch_factors(model: str, momentum: bool, settings: Settings) -> Dataset:
    """Daily factor returns (decimals): ``Mkt-RF, SMB, HML [, RMW, CMA] [, Mom], RF``.

    ``model`` is ``ff3`` or ``ff5``. With ``momentum`` the Carhart ``Mom``
    series is inner-joined (dates where every column is published).
    """
    from .market import Dataset

    key = model.lower().strip()
    if key not in ("ff3", "ff5"):
        raise ValueError(f"unknown factor model {model!r}; use 'ff3' or 'ff5'")
    base = fetch_dataset(FILES[key], settings)
    df, provs = base.data.copy(), list(base.provenance)
    if momentum:
        mom = fetch_dataset(FILES["mom"], settings)
        m = mom.data.copy()
        m.columns = ["Mom" if c.lower().startswith("mom") else c for c in m.columns]
        df = df.join(m[["Mom"]], how="inner")
        provs.extend(mom.provenance)
    cols = [c for c in FACTOR_ORDER if c in df.columns] + [c for c in df.columns if c not in FACTOR_ORDER]
    df = df[cols].dropna(how="any")
    if df.empty:
        raise DataUnavailable("french: factor tables have no overlapping dates")
    return Dataset(df, provs)
