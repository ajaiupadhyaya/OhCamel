"""Pre-registered experiments: their frozen config, its hash, and the once-only holdout rule."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

import pandas as pd
import yaml

from ..config import REPO_ROOT

EXPERIMENTS = REPO_ROOT / "research" / "experiments"
NOT_HASHED = ("holdout_reevaluation_approved",)


def load_config(exp: str) -> dict[str, Any]:
    return yaml.safe_load((EXPERIMENTS / exp / "config.yaml").read_text())


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
