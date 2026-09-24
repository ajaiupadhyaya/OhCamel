"""On-disk cache of fetched datasets, with provenance.

Layout: ``<data_dir>/<family>/<safe_key>.<parquet|json>`` plus a
``<safe_key>.meta.json`` sidecar holding the :class:`Provenance` of the copy
and ``fetched_at`` (UNIX epoch seconds). Writes are atomic (temporary file in
the same directory, then :func:`os.replace`), and the sidecar is written
*last*, so a reader never sees a payload without its provenance.

Freshness: :meth:`Store.get` returns a copy only when ``now - fetched_at <=
ttl``. When a refresh fails, :meth:`Store.fetch_or_stale` serves the last good
copy instead, with ``detail["note"] = "stale: refresh failed: <reason>"`` --
real data, just older, and labelled as such. Nothing is ever invented.

A pandas ``Series`` is stored as a one-column frame and restored as a Series;
dicts/lists are stored as JSON.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Settings
from .base import DataUnavailable, Provenance

log = logging.getLogger("ohcamel_quant.data.store")

_SAFE = re.compile(r"[^A-Za-z0-9._=-]")


def safe_key(key: str) -> str:
    """Filesystem-safe, collision-free file stem for ``key``.

    Characters outside ``[A-Za-z0-9._=-]`` become ``_``; when anything was
    replaced (or the key is long) a short SHA-1 suffix keeps distinct keys
    distinct (``^GSPC`` and ``_GSPC`` must not share a file).
    """
    s = _SAFE.sub("_", key)
    if s != key or len(s) > 120 or s.startswith("."):
        s = f"{s[:100]}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"
    return s


@dataclass
class Entry:
    obj: Any
    provenance: Provenance
    fetched_at: float  # epoch seconds

    @property
    def age_s(self) -> float:
        return max(0.0, time.time() - self.fetched_at)


def _prov_from_dict(d: dict[str, Any]) -> Provenance:
    return Provenance(
        source=d.get("source", "unknown"),
        fetched_at=d.get("fetched_at", ""),
        synthetic=bool(d.get("synthetic", False)),
        detail=dict(d.get("detail") or {}),
    )


def stale_provenance(prov: Provenance, reason: str) -> Provenance:
    """Copy of ``prov`` annotated as a stale fallback."""
    detail = dict(prov.detail)
    detail["note"] = f"stale: refresh failed: {reason}"
    return Provenance(source=prov.source, fetched_at=prov.fetched_at, synthetic=prov.synthetic, detail=detail)


def _atomic_write(path: Path, write: Callable[[Path], None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        write(tmp_path)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


class Store:
    """File cache rooted at ``root`` (usually ``Settings.data_dir``)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    # ------------------------------------------------------------ paths
    def _base(self, family: str, key: str) -> Path:
        return self.root / safe_key(family) / safe_key(key)

    def _meta_path(self, family: str, key: str) -> Path:
        b = self._base(family, key)
        return b.with_name(b.name + ".meta.json")

    # ------------------------------------------------------------- read
    def read(self, family: str, key: str) -> Entry | None:
        """The cached entry regardless of age, or None (also on corruption)."""
        meta_p = self._meta_path(family, key)
        # put() replaces the payload and then the sidecar; reading both under
        # the same lock guarantees (in this process) that payload and
        # provenance come from one write. Another process (e.g. the ``warm``
        # CLI) could still interleave, so the sidecar is re-read afterwards and
        # the read retried if it changed.
        for _ in range(5):
            if not meta_p.exists():
                return None
            try:
                with self._lock:
                    meta_text = meta_p.read_text()
                    meta = json.loads(meta_text)
                    kind = meta.get("kind", "frame")
                    base = self._base(family, key)
                    if kind == "json":
                        obj: Any = json.loads(base.with_name(base.name + ".json").read_text())
                    else:
                        df = pd.read_parquet(base.with_name(base.name + ".parquet"))
                        if kind == "series":
                            obj = df.iloc[:, 0]
                            obj.name = meta.get("series_name")
                        else:
                            obj = df
                    changed = meta_p.read_text() != meta_text
                if changed:
                    continue  # another process replaced the entry mid-read
                return Entry(obj, _prov_from_dict(meta.get("provenance", {})), float(meta["fetched_at"]))
            except Exception as e:  # noqa: BLE001 - a corrupt cache entry is a miss, not a crash
                log.warning("store: unreadable entry %s/%s: %s", family, key, e)
                return None
        log.warning("store: entry %s/%s kept changing while being read", family, key)
        return None

    def get(self, family: str, key: str, ttl: float | None) -> tuple[Any, Provenance] | None:
        """``(obj, provenance)`` if cached and ``age <= ttl`` (``ttl=None``: never
        expires), else None."""
        e = self.read(family, key)
        if e is None:
            return None
        if ttl is not None and e.age_s > ttl:
            return None
        return e.obj, e.provenance

    def get_stale(self, family: str, key: str, reason: str) -> tuple[Any, Provenance] | None:
        """Last good copy regardless of age, provenance-annotated as stale."""
        e = self.read(family, key)
        if e is None:
            return None
        return e.obj, stale_provenance(e.provenance, reason)

    # ------------------------------------------------------------ write
    def put(self, family: str, key: str, obj: Any, prov: Provenance, fetched_at: float | None = None) -> None:
        """Atomically store ``obj`` (DataFrame, Series, dict or list)."""
        base = self._base(family, key)
        meta: dict[str, Any] = {
            "family": family, "key": key, "provenance": prov.to_dict(),
            "fetched_at": time.time() if fetched_at is None else fetched_at,
        }
        with self._lock:
            if isinstance(obj, pd.Series):
                meta["kind"] = "series"
                meta["series_name"] = None if obj.name is None else str(obj.name)
                df = obj.to_frame(name="value")
                _atomic_write(base.with_name(base.name + ".parquet"), lambda p: df.to_parquet(p))
            elif isinstance(obj, pd.DataFrame):
                meta["kind"] = "frame"
                df = obj.copy()
                df.columns = [str(c) for c in df.columns]
                _atomic_write(base.with_name(base.name + ".parquet"), lambda p: df.to_parquet(p))
            elif isinstance(obj, (dict, list)):
                meta["kind"] = "json"
                text = json.dumps(obj, default=str)
                _atomic_write(base.with_name(base.name + ".json"), lambda p: p.write_text(text))
            else:
                raise TypeError(f"store: cannot persist {type(obj).__name__}")
            meta_text = json.dumps(meta, default=str)
            _atomic_write(self._meta_path(family, key), lambda p: p.write_text(meta_text))

    def delete(self, family: str, key: str) -> None:
        base = self._base(family, key)
        for suffix in (".meta.json", ".parquet", ".json"):
            base.with_name(base.name + suffix).unlink(missing_ok=True)

    # ---------------------------------------------------------- helpers
    def fetch_or_stale(
        self, family: str, key: str, ttl: float | None,
        fetch: Callable[[], tuple[Any, Provenance]],
        offline: bool = False,
    ) -> tuple[Any, Provenance]:
        """Fresh cache hit -> fetch and store -> last good copy (labelled stale).

        Raises the fetch's :class:`DataUnavailable` when there is no copy at all.
        In ``offline`` mode the network is not attempted; any cached copy is
        served (annotated stale if past its TTL).
        """
        hit = self.get(family, key, ttl)
        if hit is not None:
            return hit
        if offline:
            stale = self.get_stale(family, key, "offline mode")
            if stale is None:
                raise DataUnavailable(f"offline mode: {family}/{key} is not cached")
            return stale
        try:
            obj, prov = fetch()
        except DataUnavailable as e:
            stale = self.get_stale(family, key, str(e))
            if stale is not None:
                log.warning("store: serving stale %s/%s: %s", family, key, e)
                return stale
            raise
        self.put(family, key, obj, prov)
        return obj, prov


_stores: dict[Path, Store] = {}
_stores_lock = threading.Lock()


def get_store(settings: Settings) -> Store:
    """The process-wide store for ``settings.data_dir``."""
    root = Path(settings.data_dir)
    with _stores_lock:
        s = _stores.get(root)
        if s is None:
            s = _stores[root] = Store(root)
        return s
