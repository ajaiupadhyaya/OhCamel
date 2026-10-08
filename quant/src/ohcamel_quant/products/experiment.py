"""Pre-registered experiments: their frozen config, its hash, and the once-only holdout rule.

A universe freeze runs once, inside the deployed worker container, where the
image (and so ``config.yaml``) is read-only. It writes
``{data_dir}/experiments/<EXP>/universe.yaml`` on the persistent volume
(``/data`` on the droplet), and :func:`load_config` lays that file's
``universe`` and ``universe_frozen_on`` over a config that has none. A universe
committed to ``config.yaml`` always wins. Committing the same two lines later
leaves ``config_hash`` unchanged (docs/runbooks/compute.md).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml

from ..config import REPO_ROOT, get_settings

EXPERIMENTS = REPO_ROOT / "research" / "experiments"
NOT_HASHED = ("holdout_reevaluation_approved",)


FROZEN_DIR: Path | None = None  # tests point the freeze's state at a temp dir; default {data_dir}/experiments
FROZEN_KEYS = ("universe", "universe_frozen_on")


def frozen_universe_path(exp: str) -> Path:
    return (FROZEN_DIR or get_settings().data_dir / "experiments") / exp / "universe.yaml"


def load_config(exp: str) -> dict[str, Any]:
    cfg: dict[str, Any] = yaml.safe_load((EXPERIMENTS / exp / "config.yaml").read_text())
    path = frozen_universe_path(exp)
    if not cfg.get("universe") and path.exists():
        frozen = yaml.safe_load(path.read_text()) or {}
        cfg.update({k: frozen[k] for k in FROZEN_KEYS if k in frozen})
    return cfg


def config_hash(cfg: dict[str, Any]) -> str:
    body = {k: v for k, v in cfg.items() if k not in NOT_HASHED}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


def holdout_decision(prev_holdout: pd.DataFrame | None, *, version: int, cfg_hash: str,
                     approved: int | None) -> Literal["evaluate", "carry", "stale"]:
    """Evaluate once. Carry an evaluation made under this methodology version and config; anything else is
    stale unless the owner approved re-evaluation for this version (Review Focus 1)."""
    if prev_holdout is None or prev_holdout.empty:
        return "evaluate"
    row = prev_holdout.iloc[0]
    if int(row["methodology_version"]) == version and str(row["config_hash"]) == cfg_hash:
        return "carry"
    return "evaluate" if approved == version else "stale"
