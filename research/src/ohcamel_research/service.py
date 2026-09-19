"""The research service: one weight a strategy a trading day, computed from
its manifest's ``selected_params`` and fdq's own rule -- never a
reimplementation of it.

**Why this never reimplements the rule.** ``signal.emit`` already runs the
strategy through ``fdq.strategies.trend`` (``ohcamel_research.signal.REGISTRY``
maps ``"ma_crossover"``/``"donchian"`` to fdq's own ``MACrossover``/
``Donchian`` classes -- the exact classes ``battery/run.py`` ran to produce
the manifests this service reads). This module calls ``emit`` directly for
its weight, exactly as ``ohcamel-research signal emit`` does, so the live
signal and the backtest that validated it can never drift apart by one of
them having its own copy of "is the close above its SMA". There is nothing
here to pin against fdq's rule on the fixture bars, because nothing here
computes it a second way.

**What this module owns instead:**

- ``BarSource``, the fetch behind an interface (see its docstring) --
  ``AlpacaBarSource`` for production (fdq's own Alpaca-backed fetcher) and
  ``FixtureBarSource`` for hermetic tests (the committed ``fixtures/bars/``);
- ``ServiceConfig``/``load_service_config``, ``research/service.yaml``
  validated the way ``battery/config.py`` validates an experiment's
  ``config.yaml`` -- unknown or missing keys refused, not ignored;
- two checks *before* calling ``emit`` that ``emit`` itself has no way to
  make, because they are about this service's own config agreeing with the
  manifest, not about the manifest's freshness or verdict: the config's
  ``fdq_strategy`` must be the family the manifest was actually judged
  under (``manifest.strategy``), and the manifest's own
  ``selected_params["symbol"]`` must be the symbol the config names. Either
  mismatch is a deployment bug, not something a signal should paper over,
  so it refuses to emit anything for that strategy rather than emit a
  document computed under the wrong rule or against the wrong manifest;
- the atomic, once-a-day, idempotent write (``run_once``/``_run_strategy``),
  through ``signal.emit``/``signal.write_signal`` and nothing else;
- the schedule (``run_forever``, see its docstring for why a loop and not
  cron, which days it runs, and how a late bar is retried), its watchdog,
  and the orphaned-temp-file sweep on start.

**The weight is the fraction the evidence held, not 1.0.** fdq's engine
sizes a target weight against ``max_deployable(equity)`` =
``equity * (1 - cash_buffer_pct)``, so every backtest behind EXP-A01's
manifests held 90% of equity when the rule was on (``friction_v1.yaml``'s
``cash_buffer_pct: 0.10``). ``signal.emit``, given the manifest, multiplies
fdq's weight by ``signal.invested_fraction`` -- that same
``max_deployable(1.0)``, read from the friction file the manifest's own
experiment config names and loaded by the battery's own loader -- so a
document says 0.9 when the rule is on and carries no target when it is off.
The number is read, never written here.

**What the live fetch pins, and what it cannot** (``AlpacaBarSource``,
``fetchable_through``). The evidence is fdq's Alpaca daily bars:
consolidated (SIP), unadjusted (``fixtures/history/README.md``). fdq's
``fetch_symbol`` takes only a symbol, a start and an end *date*;
``fdq.data.bars._fetch_alpaca`` builds its ``StockBarsRequest`` with neither
``feed`` nor ``adjustment``, and nothing in ``fetch_symbol`` or fdq's
``Settings`` sets either, so neither can be pinned without modifying fdq
(which this project never does). Both therefore take Alpaca's server-side
defaults -- the ones the evidence was fetched with through the same call:
adjustment ``raw`` (Alpaca's documented default) and the account's default
feed (SIP for the account the fixtures came from, as their consolidated
volumes show). What fdq's API does allow is the end date, which fdq sends
as that date's 23:59:59.999999, read by Alpaca as UTC -- inside the last
15 minutes of SIP data a free plan may not query, and even ahead of the
clock, at 19:15 New York in summer. So the end is pinned: the fetch is never
handed a date whose 23:59:59.999999 UTC is later than now minus 16 minutes,
and the schedule waits for today's to qualify -- 00:16 UTC, which is 20:16
New York in summer and 19:16 in winter, not 19:15.
"""

from __future__ import annotations

import math
import re
import signal as os_signal
import tempfile
import time as time_module
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from ohcamel_research import REPO_ROOT
from ohcamel_research.battery.data import load_bars as _load_fixture_bars
from ohcamel_research.battery.data import slice_bars
from ohcamel_research.contract import load_schema
from ohcamel_research.manifest import Manifest
from ohcamel_research.signal import REGISTRY, emit, write_signal

__all__ = [
    "RUN_AT",
    "SIP_RECENT_HOLD",
    "WATCHDOG_SECONDS",
    "ZONE",
    "AlpacaBarSource",
    "BarSource",
    "FixtureBarSource",
    "ServiceConfig",
    "ServiceError",
    "StrategyConfig",
    "StrategyOutcome",
    "VendorMismatchError",
    "WatchdogTimeout",
    "fetchable_through",
    "load_service_config",
    "main",
    "run_day",
    "run_forever",
    "run_once",
    "sweep_orphaned_tmp",
]

# The signal directory the image's contract with the compose volume fixes
# (see deploy/docker-compose.yml: ohcamel-research mounts the named
# `signals` volume read-write here). Not an environment variable: nothing
# about which strategies run or when is meant to vary by an env var except
# the Alpaca keys fdq's own Settings already reads -- see AlpacaBarSource.
SIGNALS_DIR = Path("/signals")

# 19:15 America/New_York (design and Task 16's brief): after the desk has
# recorded the close (Task 14 defers anything earlier) and inside Alpaca's
# opening-auction window for the *next* session's Opg orders. The earliest
# a day's run starts; the fetch's end pin (SIP_RECENT_HOLD) can hold it later.
RUN_AT = time(19, 15)
ZONE = ZoneInfo("America/New_York")

# How far behind now the end of a fetch must be. Alpaca refuses a free plan
# SIP data from the last 15 minutes; one more minute keeps a poll that lands
# exactly on the boundary out of it. See fetchable_through.
SIP_RECENT_HOLD = timedelta(minutes=16)

# The watchdog around one run of every strategy (run_forever). Generous: a
# healthy run is a couple of small fetches and a few hundred bars through
# fdq, seconds in all; this exists only so a fetch that never returns cannot
# stop the loop.
WATCHDOG_SECONDS = 900.0

# How much extra history, in trading days, beyond the longest moving
# average a config's selected_params implies. fdq's long/flat strategies
# are state machines walked forward from the first bar handed to them
# (see signal.emit's module docstring), but their *terminal* state -- the
# only one target_weights reads -- depends only on the final day's own
# comparison (fast_ma vs slow_ma for MACrossover; the Donchian channel test
# for Donchian), so this margin exists only to guarantee the SMA/channel
# itself is not still NaN on the last bar, not to reproduce history.
LOOKBACK_MARGIN_BARS = 30

# The schema's own definition of a valid strategy slug (interface/
# signal.schema.json's `strategy` property) -- read once, rather than a
# second regex maintained by hand here, so this file's notion of a valid
# slug can never drift from what emit()'s own schema check (contract.check)
# would refuse anyway.
_SLUG_PATTERN = re.compile(load_schema()["properties"]["strategy"]["pattern"])
_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{0,9}$")

# fdq_strategy values this service will actually run, out of everything
# signal.REGISTRY knows. Donchian's _desired reads a rolling high/low
# channel AND -- when the price is inside it -- falls back to self._current,
# its own carried-forward state (fdq.strategies.trend.Donchian._desired):
# unlike MACrossover, whose terminal state depends only on the final day's
# own comparison (see LOOKBACK_MARGIN_BARS above), Donchian's terminal state
# can depend on how far back the walk-forward in signal.emit actually
# started. This service's lookback window is sized to cover the longest
# moving average plus a margin, not "however far back a channel's carried
# state might trace" -- a truncated lookback would compute Donchian's state
# wrongly, silently. Refused until that window is anchored to enough
# history for Donchian specifically.
_SUPPORTED_FDQ_STRATEGIES = ("ma_crossover",)

_SERVICE_CONFIG_REQUIRED = ("strategies",)
_STRATEGY_REQUIRED = ("slug", "fdq_strategy", "symbol", "manifest")


class ServiceError(RuntimeError):
    """A config the service refuses to run with, or a strategy it refuses
    to emit for on a particular day. Never silently worked around."""


# ---------------------------------------------------------------------------
# The fetch, behind an interface.
# ---------------------------------------------------------------------------


class BarSource(Protocol):
    """Where the service's bars come from. One method, because that is all
    ``_run_strategy`` needs: every symbol this service ever asks for is a
    single ticker, and every caller wants long-form rows it can hand
    straight to ``signal.emit`` as ``bars_long``.

    A conforming ``fetch`` returns columns ``date`` (``datetime.date``),
    ``symbol``, ``open``, ``high``, ``low``, ``close``, ``volume``, sorted
    by date, covering at least every trading day in ``[start, end]`` that
    the source actually has (a source may return bars outside that range
    too; ``emit`` filters to ``as_of`` itself, and the caller here slices
    again before deciding ``as_of``). An empty frame means "nothing for
    this symbol in this range", never an exception -- the caller decides
    what an empty result means.
    """

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...


def _to_long_form(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """fdq's per-symbol frame (a ``DatetimeIndex`` and ``open``/``high``/
    ``low``/``close``/``volume`` columns, ``fdq.data.bars.fetch_symbol``'s
    own shape) reshaped to ``battery.data.load_bars``'s long form, so the
    rest of this module -- and ``signal.emit`` -- never has to know which
    ``BarSource`` produced a frame."""
    cols = ["open", "high", "low", "close", "volume"]
    out = df.loc[:, [c for c in cols if c in df.columns]].copy()
    out = out.reset_index()
    out = out.rename(columns={out.columns[0]: "date"})
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out["symbol"] = symbol
    for c in cols:
        if c not in out.columns:
            out[c] = float("nan")
    return out[["date", "symbol", *cols]].sort_values("date").reset_index(drop=True)


class VendorMismatchError(ServiceError):
    """fdq did not answer this fetch from Alpaca -- see
    ``_require_alpaca_provenance``. Never worked around: a strategy this
    hits is skipped for the day, and nothing is written for it."""


def _require_alpaca_provenance(cache_parquet: Path, symbol: str) -> None:
    """Refuse a fetch that did not come from Alpaca.

    At the pinned commit, ``fdq.data.bars.fetch_symbol`` falls back to
    yfinance -- silently, from this function's point of view -- whenever
    the Alpaca call itself fails or comes back empty for the requested
    range (``fdq/data/bars.py``, ``fetch_symbol``, roughly lines 176-186).
    The ``DataFrame`` it returns carries no marker of which vendor actually
    answered; the one place fdq *does* record that, for this exact call, is
    the provenance sidecar its own cache write leaves behind
    (``fdq.data.provenance.write_provenance``, at
    ``<cache_parquet>.meta.json``) -- its ``"source"`` field is exactly what
    this call just wrote to disk, because ``fetch_symbol`` always rewrites
    the sidecar after a successful fetch, cache hit or not.

    A source other than ``"alpaca"``, or no sidecar at all (unreadable,
    missing, malformed), means this fetch cannot be trusted to be Alpaca's
    own data -- refused here, before a single bar reaches ``emit``, rather
    than silently computing a live weight from whatever fdq happened to
    fall back to. Never reads or logs a credential: nothing here touches
    ``ALPACA_API_KEY``/``ALPACA_SECRET_KEY`` or fdq's ``Settings`` at all.
    """
    from fdq.data.provenance import read_provenance as fdq_read_provenance

    try:
        doc = fdq_read_provenance(cache_parquet)
    except Exception as e:  # noqa: BLE001 -- any failure to read means "not provably alpaca"
        raise VendorMismatchError(
            f"{symbol}: no readable provenance sidecar after fetch "
            f"({type(e).__name__}: {e}); refusing rather than risk an unmarked vendor"
        ) from e
    source = doc.get("source")
    if source != "alpaca":
        raise VendorMismatchError(
            f"{symbol}: fdq answered this fetch from {source!r}, not alpaca; refusing"
        )


def _fdq_end_instant(day: date) -> datetime:
    """The instant fdq asks Alpaca for when handed ``end=day``:
    ``datetime.combine(end, datetime.max.time())`` in
    ``fdq.data.bars._fetch_alpaca``, a naive datetime alpaca-py documents as
    UTC."""
    return datetime.combine(day, time.max, tzinfo=UTC)


def fetchable_through(now: datetime) -> date:
    """The latest end date fdq's fetch may be handed at ``now`` (aware):
    the latest day whose ``_fdq_end_instant`` is at least ``SIP_RECENT_HOLD``
    before ``now``. fdq's API takes the end only as a date, so this is the
    one part of the request's end this service can pin; see the module
    docstring."""
    cutoff = now.astimezone(UTC) - SIP_RECENT_HOLD
    day = cutoff.date()
    return day if _fdq_end_instant(day) <= cutoff else day - timedelta(days=1)


def _fetchable_from(day: date) -> datetime:
    """The first instant ``fetchable_through`` reaches ``day``: the next
    UTC midnight (one microsecond after ``_fdq_end_instant(day)``) plus
    ``SIP_RECENT_HOLD`` -- 00:16 UTC."""
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC) + SIP_RECENT_HOLD


class AlpacaBarSource:
    """Production: fdq's own Alpaca-backed daily-bar fetcher
    (``fdq.data.bars.fetch_symbol``), which reads ``ALPACA_API_KEY`` and
    ``ALPACA_SECRET_KEY`` from the process environment through
    ``fdq.util.settings.Settings`` -- this class never reads either
    variable itself, and never logs or prints one. Those are the *data*
    keys the live engine's own ``env_file`` also carries (see
    ``deploy/docker-compose.yml``); the *trading* keys in that same file
    (``ALPACA_TRADING_API_KEY``/``_SECRET_KEY``) are simply never read here.

    ``cross_validate=False``: fdq's default cross-checks every fetch against
    yfinance and logs discrepancies to a quality directory. That is a
    data-quality report, not something a once-a-day weight computation
    needs, and it is one more network call and one more place a fetch that
    would otherwise have succeeded can fail.

    Every fetch is checked with ``_require_alpaca_provenance`` before its
    bars are handed back -- see that function for why.

    **A fresh cache for every fetch.** ``fetch_symbol`` merges what it
    fetched into whatever ``<FDQ_DATA_DIR>/raw/<symbol>.parquet`` already
    holds and rewrites the sidecar with *this* fetch's source. With one
    shared cache, rows a refused yfinance fallback wrote yesterday would
    survive under today's ``alpaca`` sidecar and pass the provenance check.
    Each fetch therefore gets its own empty data directory, created inside
    the configured ``FDQ_DATA_DIR`` (the tmpfs, in the container) and
    deleted when the fetch returns: the sidecar read afterwards describes
    every row handed back, and nothing outlives the call.

    **The end is pinned** (``fetchable_through``): ``end`` is lowered, never
    raised, to the latest date whose 23:59:59.999999 UTC -- the instant fdq
    asks Alpaca for -- is at least ``SIP_RECENT_HOLD`` before ``clock()``.
    ``feed`` and ``adjustment`` cannot be pinned through fdq (see the module
    docstring) and take Alpaca's defaults.
    """

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        from fdq.data.bars import fetch_symbol
        from fdq.util.settings import Settings

        end = min(end, fetchable_through(self._clock()))
        parent = Settings().data_dir
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fetch-", dir=parent) as fresh:
            settings = Settings(FDQ_DATA_DIR=fresh)
            df = fetch_symbol(symbol, start, end, settings=settings, cross_validate=False)
            _require_alpaca_provenance(settings.raw_dir / f"{symbol}.parquet", symbol)
            return _to_long_form(df, symbol)


class FixtureBarSource:
    """Hermetic tests' fake: real, committed history under
    ``fixtures/bars/`` (Alpaca-sourced, provenance-checked -- see
    ``battery.data.read_provenance``), never a network call and never a
    credential. Every service test fetches through this."""

    def __init__(self, fixtures_dir: Path) -> None:
        self._fixtures_dir = fixtures_dir

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        bars = _load_fixture_bars(self._fixtures_dir, [symbol])
        return slice_bars(bars, start=start, end=end, symbols=[symbol])


# ---------------------------------------------------------------------------
# research/service.yaml, validated.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyConfig:
    slug: str
    fdq_strategy: str
    symbol: str
    manifest: str  # repo-root-relative path, as given in the YAML


@dataclass(frozen=True)
class ServiceConfig:
    strategies: tuple[StrategyConfig, ...]


def _mapping(v: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(v, dict):
        raise ServiceError(f"{where}: expected a mapping")
    return v


def _exact_keys(m: Mapping[str, Any], keys: tuple[str, ...], where: str) -> None:
    missing = [k for k in keys if k not in m]
    if missing:
        raise ServiceError(f"{where}: missing key {missing[0]!r}")
    unknown = sorted(set(m) - set(keys))
    if unknown:
        raise ServiceError(f"{where}: unknown key {unknown[0]!r}")


def _str(v: Any, where: str) -> str:
    if not isinstance(v, str) or not v:
        raise ServiceError(f"{where}: expected a non-empty string")
    return v


def _strategy_config(entry: Any, where: str) -> StrategyConfig:
    e = _mapping(entry, where)
    _exact_keys(e, _STRATEGY_REQUIRED, where)

    slug = _str(e["slug"], f"{where}.slug")
    if not _SLUG_PATTERN.match(slug):
        raise ServiceError(f"{where}.slug: {slug!r} does not match {_SLUG_PATTERN.pattern!r}")

    fdq_strategy = _str(e["fdq_strategy"], f"{where}.fdq_strategy")
    if fdq_strategy not in REGISTRY:
        raise ServiceError(
            f"{where}.fdq_strategy: {fdq_strategy!r} is not one of {sorted(REGISTRY)}"
        )
    if fdq_strategy not in _SUPPORTED_FDQ_STRATEGIES:
        raise ServiceError(
            f"{where}.fdq_strategy: {fdq_strategy!r} is a real fdq strategy but not yet "
            f"supported by this service -- only {_SUPPORTED_FDQ_STRATEGIES!r} is, until "
            "donchian's window is anchored to enough history (its state can depend on "
            "history a truncated lookback would compute wrongly)"
        )

    symbol = _str(e["symbol"], f"{where}.symbol")
    if not _SYMBOL_PATTERN.match(symbol):
        raise ServiceError(f"{where}.symbol: {symbol!r} is not a ticker")

    manifest = _str(e["manifest"], f"{where}.manifest")
    if manifest.startswith("/") or ".." in Path(manifest).parts:
        raise ServiceError(f"{where}.manifest: {manifest!r} must be a path inside the repository")

    return StrategyConfig(slug=slug, fdq_strategy=fdq_strategy, symbol=symbol, manifest=manifest)


def load_service_config(path: Path, repo_root: Path = REPO_ROOT) -> ServiceConfig:
    """Read and validate ``research/service.yaml``: which strategies this
    service runs, each one's slug, fdq's strategy key for it, its symbol
    and its manifest's path. Every manifest named must exist under
    ``repo_root`` -- a config naming one that does not is refused at
    startup, not discovered the first time that strategy's turn comes up a
    day later. Slugs must be unique and match the schema's own strategy
    pattern; ``fdq_strategy`` must be a key ``signal.REGISTRY`` knows."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ServiceError(f"{path}: cannot be read ({e})") from e

    d = _mapping(doc, "service.yaml")
    missing = [k for k in _SERVICE_CONFIG_REQUIRED if k not in d]
    if missing:
        raise ServiceError(f"service.yaml: missing key {missing[0]!r}")
    unknown = sorted(set(d) - set(_SERVICE_CONFIG_REQUIRED))
    if unknown:
        raise ServiceError(f"service.yaml: unknown key {unknown[0]!r}")

    raw = d["strategies"]
    if not isinstance(raw, list) or not raw:
        raise ServiceError("service.yaml: strategies must be a non-empty list")

    strategies = tuple(_strategy_config(e, f"strategies[{i}]") for i, e in enumerate(raw))

    slugs = [s.slug for s in strategies]
    if len(set(slugs)) != len(slugs):
        raise ServiceError("service.yaml: strategies[].slug must be unique")

    for s in strategies:
        if not (repo_root / s.manifest).is_file():
            raise ServiceError(
                f"strategies[]: manifest {s.manifest!r} (slug {s.slug!r}) does not exist "
                f"under {repo_root}"
            )

    return ServiceConfig(strategies=strategies)


# ---------------------------------------------------------------------------
# The lookback window.
# ---------------------------------------------------------------------------


def _lookback_period(params: Mapping[str, Any]) -> int:
    """The longest period a manifest's ``selected_params`` implies: the
    ``slow``/``fast`` pair for ``ma_crossover``, the ``window`` for
    ``donchian`` -- generalised as "the largest numeric parameter that
    is not the symbol", so this needs no per-fdq-strategy special case."""
    periods = [
        v
        for k, v in params.items()
        if k != "symbol" and isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    if not periods:
        raise ServiceError(f"selected_params {dict(params)!r} has no numeric period")
    return int(max(periods))


def _lookback_start(today: date, period: int, margin_bars: int = LOOKBACK_MARGIN_BARS) -> date:
    """``today`` minus enough calendar days to cover ``period + margin_bars``
    trading days -- roughly 7/5 calendar days per trading day, plus a flat
    15-day buffer for holidays. Generous on purpose: ``emit`` slices to
    ``as_of`` itself, so fetching more history than strictly needed costs
    nothing but a slightly larger fetch, while fetching too little would
    leave the moving average NaN on the very day this service needs it."""
    trading_days = period + margin_bars
    calendar_days = math.ceil(trading_days * 7 / 5) + 15
    return today - timedelta(days=calendar_days)


# ---------------------------------------------------------------------------
# One strategy, one day.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyOutcome:
    """What one strategy's turn in a run came to. ``as_of`` is the newest
    bar's date the fetch returned -- ``today`` itself when today's document
    already existed and nothing was fetched -- or ``None`` when the strategy
    was refused or raised before any bar was seen. ``written`` is the
    document written on this turn, if any."""

    slug: str
    as_of: date | None
    written: Path | None


def _signal_path(signals_dir: Path, slug: str, day: date) -> Path:
    return signals_dir / f"{slug}-{day.isoformat()}.json"


def _late_line(outcome: StrategyOutcome, today: date) -> str:
    return (
        f"research  {outcome.slug}: the newest bar is {outcome.as_of}, not {today} -- a late "
        f"bar, or no session on {today}; no signal for {today} yet"
    )


def _run_strategy(
    cfg: StrategyConfig,
    *,
    bar_source: BarSource,
    today: date,
    signals_dir: Path,
    repo_root: Path,
    on_event: Callable[[str], None],
) -> StrategyOutcome:
    """Refresh, compute, emit, write -- for one strategy, once. Nothing is
    fetched when ``today``'s document already exists. Otherwise a document
    is written for the newest bar's date unless one exists for it already
    (a same-``as_of`` re-run is a no-op) or the strategy is refused -- the
    manifest cannot be trusted enough even to compute a weight from, or the
    fetch came back empty. Every refusal is logged through ``on_event`` and
    never raised past this function for the caller (``run_day``) to treat as
    this one strategy's problem alone. A newest bar older than ``today`` is
    not logged here: the caller decides how often to say so."""
    if _signal_path(signals_dir, cfg.slug, today).exists():
        return StrategyOutcome(cfg.slug, today, None)

    manifest_path = repo_root / cfg.manifest
    try:
        manifest = Manifest.load(manifest_path)
    except Exception as e:  # noqa: BLE001 -- any load failure means no params to compute from
        on_event(
            f"research  {cfg.slug}: cannot load manifest {manifest_path} "
            f"({type(e).__name__}: {e}); no signal emitted"
        )
        return StrategyOutcome(cfg.slug, None, None)

    # The family check emit() itself has no way to make: it takes
    # fdq_strategy and a manifest path as independent arguments and trusts
    # the caller to have paired them correctly. Refusing here, before any
    # bars are fetched, stops this config ever computing SPY's weight under
    # a manifest judged on a different rule (or vice versa).
    if manifest.strategy != cfg.fdq_strategy:
        on_event(
            f"research  {cfg.slug}: refusing -- config's fdq_strategy {cfg.fdq_strategy!r} "
            f"does not match the manifest's strategy {manifest.strategy!r}"
        )
        return StrategyOutcome(cfg.slug, None, None)

    manifest_symbol = manifest.selected_params.get("symbol")
    if manifest_symbol != cfg.symbol:
        on_event(
            f"research  {cfg.slug}: refusing -- the manifest's selected_params symbol "
            f"{manifest_symbol!r} does not match the config's symbol {cfg.symbol!r}"
        )
        return StrategyOutcome(cfg.slug, None, None)

    period = _lookback_period(manifest.selected_params)
    start = _lookback_start(today, period)
    bars_long = bar_source.fetch(cfg.symbol, start, today)
    if bars_long.empty:
        on_event(
            f"research  {cfg.slug}: no bars for {cfg.symbol} in [{start}, {today}]; "
            "no signal emitted"
        )
        return StrategyOutcome(cfg.slug, None, None)

    as_of = max(bars_long["date"])
    out = _signal_path(signals_dir, cfg.slug, as_of)
    if out.exists():
        return StrategyOutcome(cfg.slug, as_of, None)  # as_of's document exists: a no-op

    doc = emit(
        cfg.slug,
        cfg.fdq_strategy,
        manifest.selected_params,
        as_of,
        bars_long,
        int(as_of.strftime("%Y%m%d")),
        validation_from=manifest_path,
        repo_root=repo_root,
    )
    write_signal(doc, out)
    on_event(
        f"research  {cfg.slug}: wrote {out.name}, status={doc['validation']['status']}, "
        f"targets={doc['targets']}"
    )
    return StrategyOutcome(cfg.slug, as_of, out)


def run_day(
    config: ServiceConfig,
    *,
    bar_source: BarSource,
    today: date,
    signals_dir: Path,
    repo_root: Path = REPO_ROOT,
    on_event: Callable[[str], None] = lambda _line: None,
) -> list[StrategyOutcome]:
    """Every strategy in ``config``, once, for ``today``, one outcome each
    in the config's order. A strategy that raises is logged and skipped
    (``as_of`` ``None``); the rest still run -- one strategy's broken
    manifest or fetch is not a reason to withhold every other strategy's
    signal. ``WatchdogTimeout`` is a ``BaseException`` and is not caught
    here: it abandons the whole run."""
    outcomes: list[StrategyOutcome] = []
    for cfg in config.strategies:
        try:
            outcome = _run_strategy(
                cfg,
                bar_source=bar_source,
                today=today,
                signals_dir=signals_dir,
                repo_root=repo_root,
                on_event=on_event,
            )
        except Exception as e:  # noqa: BLE001 -- one strategy's bug must not sink the others
            on_event(f"research  {cfg.slug}: raised ({type(e).__name__}: {e}); no signal emitted")
            outcome = StrategyOutcome(cfg.slug, None, None)
        outcomes.append(outcome)
    return outcomes


def run_once(
    config: ServiceConfig,
    *,
    bar_source: BarSource,
    today: date,
    signals_dir: Path,
    repo_root: Path = REPO_ROOT,
    on_event: Callable[[str], None] = lambda _line: None,
) -> list[Path]:
    """``run_day``, for a caller that runs a day once (a test, a manual
    run): returns the documents written, and logs every strategy whose
    newest bar is older than ``today``."""
    outcomes = run_day(
        config,
        bar_source=bar_source,
        today=today,
        signals_dir=signals_dir,
        repo_root=repo_root,
        on_event=on_event,
    )
    for o in outcomes:
        if o.as_of is not None and o.as_of < today:
            on_event(_late_line(o, today))
    return [o.written for o in outcomes if o.written is not None]


# ---------------------------------------------------------------------------
# Orphaned temp files.
# ---------------------------------------------------------------------------


def sweep_orphaned_tmp(signals_dir: Path) -> list[Path]:
    """Remove every ``.<name>.tmp`` file directly under ``signals_dir``:
    exactly what ``signal.write_signal``'s fsync-then-rename leaves behind
    when the process dies between the two. The intake never reads a
    dotfile and never will, so nothing else ever cleans these up. Only
    files matching that exact naming convention -- a dotfile ending in
    ``.tmp`` -- are touched; never anything ending in ``.json``."""
    removed: list[Path] = []
    if not signals_dir.is_dir():
        return removed
    for p in sorted(signals_dir.iterdir()):
        if p.is_file() and p.name.startswith(".") and p.name.endswith(".tmp"):
            p.unlink()
            removed.append(p)
    return removed


# ---------------------------------------------------------------------------
# The schedule.
# ---------------------------------------------------------------------------


def _due(now_et: datetime, last_run: date | None) -> bool:
    """Once a weekday: true from the first check at or after 19:15
    America/New_York on a Monday-to-Friday date this loop has not yet
    finished (``last_run``). Pure and independent of ``run_forever``'s own
    loop, so the trigger logic is testable without touching a clock, a
    sleep, or a fetch."""
    return now_et.weekday() < 5 and now_et.time() >= RUN_AT and now_et.date() != last_run


def _previous_weekday(day: date) -> date:
    d = day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


class WatchdogTimeout(BaseException):
    """A run outlived its watchdog. A ``BaseException``, like
    ``KeyboardInterrupt``, so that no ``except Exception`` on the way --
    ``run_day``'s per-strategy guard, fdq's own vendor fallback -- can
    swallow it and carry on inside the run it is meant to abandon."""


@contextmanager
def _watchdog(seconds: float) -> Iterator[None]:
    """Raise ``WatchdogTimeout`` inside the body if it runs longer than
    ``seconds``: ``SIGALRM`` through ``setitimer``, so a fetch blocked in a
    socket read is interrupted too (the syscall returns to Python, which
    runs the handler). Main thread only, which is where the service runs.
    The previous handler is restored and the timer cancelled on every exit."""

    def _expire(_signum: int, _frame: object) -> None:
        raise WatchdogTimeout(f"no return within {seconds:g} s")

    previous = os_signal.signal(os_signal.SIGALRM, _expire)
    os_signal.setitimer(os_signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        try:
            os_signal.setitimer(os_signal.ITIMER_REAL, 0)
        finally:
            os_signal.signal(os_signal.SIGALRM, previous)


def run_forever(
    config: ServiceConfig,
    *,
    bar_source: BarSource,
    signals_dir: Path,
    repo_root: Path = REPO_ROOT,
    clock: Callable[[], datetime] = lambda: datetime.now(ZONE),
    sleep: Callable[[float], None] = time_module.sleep,
    poll_seconds: float = 300.0,
    watchdog_seconds: float = WATCHDOG_SECONDS,
    on_event: Callable[[str], None] = print,
) -> None:
    """The schedule is a loop inside this process, not cron in the
    container.

    A cron daemon would be a second process compose does not restart, does
    not health-check and does not capture the output of into the one log
    stream ``docker logs`` already shows for every other service; a failed
    fetch under cron is a mail to nobody, in a container with no MTA. A
    plain poll loop needs none of that: it is the one Python process the
    image already runs, its failure is the container's failure (so
    ``restart: unless-stopped`` already covers it), and every moving part
    -- the clock, the sleep, the fetch -- is already a parameter a test can
    replace, which a cron line inside the container image would not be.

    Runs forever (``while True``); callers other than the container's own
    entrypoint should call ``run_once`` directly with a fixed ``today``.
    Sweeps orphaned temp files once, at start, then:

    - **Back-fill, once, at start.** Started between midnight and 19:15 New
      York, with the previous weekday's document missing for any strategy:
      that session is run once (``today`` = the previous weekday), since
      its market-on-open window is still open. The desk's calendar check
      (``desk/rebalance.ml``'s ``calendar_refusal``) accepts it until 16:00
      New York on the next weekday, and all weekend; one back-filled
      between 16:00 and 19:15 on a weekday is refused there -- closed, not
      acted on -- and that evening's own run follows it.
    - **Weekends are skipped**, said once a day.
    - **A weekday's run** starts at the first poll at or after 19:15
      (``_due``), once the fetch's end pin reaches today
      (``fetchable_through``; the wait is said once). ``last_run`` is set --
      ending the day's attempts -- only once every strategy's newest bar is
      today's. Until then every poll retries, while the evening lasts: a
      late bar is picked up when it lands, a strategy that failed is tried
      again, and a strategy whose document for today exists is not fetched
      again. A newest bar older than today (a late bar, or a weekday with
      no session) is said once per strategy, day and bar -- never silently.
    - **A watchdog** (``watchdog_seconds``, ``SIGALRM``) bounds every run:
      a fetch that never returns is abandoned, logged, and the loop moves
      on to its next poll, which retries.
    """
    removed = sweep_orphaned_tmp(signals_dir)
    if removed:
        on_event(f"research  swept {len(removed)} orphaned temp file(s) on start")

    said: set[tuple[Any, ...]] = set()

    def say_once(key: tuple[Any, ...], line: str) -> None:
        if key not in said:
            said.add(key)
            on_event(line)

    def attempt(day: date) -> list[StrategyOutcome] | None:
        try:
            with _watchdog(watchdog_seconds):
                outcomes = run_day(
                    config,
                    bar_source=bar_source,
                    today=day,
                    signals_dir=signals_dir,
                    repo_root=repo_root,
                    on_event=on_event,
                )
        except WatchdogTimeout:
            on_event(
                f"research  {day}: the run outlived its {watchdog_seconds:g} s watchdog and "
                "was abandoned; the next poll retries"
            )
            return None
        for o in outcomes:
            if o.as_of is not None and o.as_of < day:
                say_once(("late", o.slug, day, o.as_of), _late_line(o, day))
        return outcomes

    started = clock().astimezone(ZONE)
    if started.time() < RUN_AT:
        session = _previous_weekday(started.date())
        missing = [
            s.slug
            for s in config.strategies
            if not _signal_path(signals_dir, s.slug, session).exists()
        ]
        if missing:
            on_event(
                f"research  started {started.isoformat()}, before {RUN_AT:%H:%M}, with no "
                f"signal for {session} from {', '.join(missing)}: running that session once"
            )
            attempt(session)

    last_run: date | None = None
    while True:
        now_et = clock().astimezone(ZONE)
        today = now_et.date()
        if today.weekday() >= 5:
            say_once(("weekend", today), f"research  {today} is a {today:%A}: no session")
        elif _due(now_et, last_run):
            if fetchable_through(now_et) < today:
                ready = _fetchable_from(today).astimezone(ZONE)
                say_once(
                    ("hold", today),
                    f"research  {today}: holding until {ready:%H:%M %Z} -- fdq asks Alpaca for "
                    "bars through 23:59:59 UTC of the end date, and a free plan may not query "
                    "SIP data from the last 15 minutes",
                )
            else:
                say_once(("run", today), f"research  {now_et.isoformat()}: running {today}")
                outcomes = attempt(today)
                if outcomes is not None and all(o.as_of == today for o in outcomes):
                    last_run = today
        sleep(poll_seconds)


def main() -> int:
    """The container's entrypoint (see ``deploy/research.Dockerfile``):
    ``research/service.yaml``'s strategies, forever, against Alpaca through
    fdq, writing to ``/signals``. No flags and no service-specific
    environment variables of its own -- what varies between a workstation
    and the container is the Alpaca keys ``AlpacaBarSource`` reads through
    fdq's ``Settings``, not this process's own configuration surface."""
    config = load_service_config(REPO_ROOT / "research" / "service.yaml", REPO_ROOT)
    run_forever(
        config,
        bar_source=AlpacaBarSource(),
        signals_dir=SIGNALS_DIR,
        repo_root=REPO_ROOT,
        on_event=print,
    )
    return 0  # pragma: no cover -- run_forever never returns


if __name__ == "__main__":
    raise SystemExit(main())
