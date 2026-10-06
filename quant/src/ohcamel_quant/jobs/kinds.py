"""The job kinds the worker can run, and what each declares.

A kind names its handler as a dotted ``module:function`` string, so the
worker's parent process can read a kind's memory class and retry count
without importing the handler (and pandas with it). Only the child imports
handlers. ``public`` kinds may be submitted through ``POST /api/jobs``
(B5); ``validate`` names a pydantic model the API checks params against.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class UnknownKind(ValueError):
    pass


@dataclass(frozen=True)
class KindSpec:
    name: str
    handler: str              # "package.module:function"
    mem_class: str            # 'S' | 'M' | 'L' (contract II.2)
    heavy: bool
    public: bool = False
    validate: str | None = None   # "package.module:PydanticModel"

    @property
    def retries(self) -> int:
        return retries_for(self.name)


REGISTRY: dict[str, KindSpec] = {}


def retries_for(kind: str) -> int:
    """Compute plan B1: 2 retries for ingest kinds, 0 for everything else."""
    return 2 if kind.startswith("ingest.") else 0


def register(spec: KindSpec) -> KindSpec:
    if spec.mem_class not in ("S", "M", "L"):
        raise ValueError(f"{spec.name}: mem_class must be S, M or L")
    if spec.name in REGISTRY and REGISTRY[spec.name] != spec:
        raise ValueError(f"kind {spec.name!r} is already registered differently")
    REGISTRY[spec.name] = spec
    return spec


def get_kind(name: str) -> KindSpec:
    try:
        return REGISTRY[name]
    except KeyError:
        raise UnknownKind(f"unknown job kind {name!r}") from None


def _resolve(dotted: str) -> Any:
    module, _, attr = dotted.partition(":")
    return getattr(importlib.import_module(module), attr)


def load_handler(spec: KindSpec) -> Callable[..., Any]:
    return _resolve(spec.handler)


def validate_params(spec: KindSpec, params: dict[str, Any]) -> None:
    """Raise ValueError when ``params`` do not satisfy the kind's model (API submissions)."""
    if spec.validate is None:
        return
    model = _resolve(spec.validate)
    try:
        model(**params)
    except Exception as e:  # pydantic.ValidationError is a ValueError subclass; keep the message
        raise ValueError(f"invalid params for {spec.name}: {e}") from e
